# Cobalt как основной backend TikTok с fallback на yt-dlp

**Дата:** 2026-09-30  
**Статус:** согласованный дизайн  
**Область:** только публичные TikTok-видео

## 1. Цель

Сделать загрузку публичных TikTok-видео устойчивее к изменениям сайта. Бот должен
сначала использовать собственный Cobalt-сервис, а при его недоступности или ошибке
автоматически пробовать существующий `yt-dlp`.

YouTube продолжает использовать `yt-dlp` для метаданных, выбора качества и загрузки.
Instagram, VK, RuTube и Pinterest в текущий релиз не входят: архитектура должна
позволять подключить их позднее без переделки очереди, временного хранилища и
Telegram upload pipeline.

## 2. Критерии успеха

- Публичное TikTok-видео загружается через Cobalt без чтения целого файла в RAM.
- При отказе Cobalt в рамках той же задачи автоматически запускается `yt-dlp`.
- Частичный файл первого backend-а удаляется до запуска второго.
- YouTube сохраняет существующее поведение и выбор доступного качества.
- Недоступность Cobalt не мешает запуску бота и работе YouTube.
- Cobalt доступен только внутри Docker network и не публикует порт на хост.
- Все ограничения очереди, подписки, rate limit, timeout, размера и cleanup
  продолжают действовать.
- В PostgreSQL для успешной загрузки сохраняется фактически использованный backend.
- Локальный `.env` и `.env.example` содержат необходимые настройки, но секреты не
  попадают в Git или логи.

## 3. Не входит в текущий релиз

- Instagram, VK, RuTube и Pinterest.
- TikTok-фотокарусели и отдельная аудиодорожка.
- Публичный Cobalt API или доступ к нему извне Docker network.
- Автоматическое обновление Cobalt через Watchtower.
- Сторонние публичные Cobalt-инстансы.
- Обход приватных, удалённых, возрастных и региональных ограничений.
- Гарантия доступности при блокировке TikTok исходящего IP сервера.

## 4. Выбранный подход

Используется гибридная маршрутизация:

```text
YouTube
  metadata: yt-dlp
  download: yt-dlp

TikTok
  download: Cobalt
       ↓ ошибка / timeout / неподдерживаемый ответ
  fallback: yt-dlp
```

Cobalt запускается как отдельный self-hosted контейнер. Это не библиотека внутри
Python-процесса и не внешний публичный API. Бот взаимодействует с ним по HTTP внутри
Docker network.

## 5. Компоненты

### 5.1 MediaBackend

Вводится внутренний протокол backend-а загрузки. Он не отвечает за проверку URL,
очередь, Telegram и очистку каталога.

```python
class MediaBackend(Protocol):
    name: DownloadBackend

    async def download(
        self,
        request: MediaDownloadRequest,
        job_dir: Path,
        progress: ProgressCallback,
    ) -> MediaPackage: ...
```

Реализации:

- `YtDlpBackend` — адаптер над существующим `MediaDownloader`;
- `CobaltBackend` — Cobalt API client и потоковая запись результата.

### 5.2 MediaBackendRouter

Router хранит порядок backend-ов по источнику:

```text
youtube -> [ytdlp]
tiktok  -> [cobalt, ytdlp]
```

Он выполняет попытки последовательно. После неуспешной попытки router удаляет только
файлы внутри текущего UUID job-каталога, оставляя сам каталог для fallback. Отмена
задачи и общий timeout не превращаются в fallback: они немедленно прерывают pipeline.

### 5.3 CobaltClient

Клиент выполняет `POST /` с заголовками `Accept: application/json` и
`Content-Type: application/json`. Для TikTok передаются:

```json
{
  "url": "https://www.tiktok.com/@account/video/id",
  "downloadMode": "auto",
  "videoQuality": "max",
  "allowH265": false,
  "disableMetadata": false,
  "alwaysProxy": true,
  "localProcessing": "disabled"
}
```

`alwaysProxy` уменьшает число прямых переходов к сторонним CDN, но клиент не должен
доверять типу ответа без проверки.

Поддерживаемые ответы:

- `tunnel` — основной успешный вариант;
- `redirect` — допустим только после полной SSRF-проверки URL и каждого redirect hop;
- `picker` — принимается только элемент типа `video`; photo-only публикация возвращает
  ошибку неподдерживаемого формата;
- `error` — преобразуется в типизированную ошибку backend-а;
- `local-processing` — в текущем релизе не поддерживается и запускает fallback;
- неизвестный статус, невалидный JSON или неполная схема — ошибка backend-а и fallback.

### 5.4 Потоковый загрузчик Cobalt

HTTP-ответ читается блоками и сразу записывается в
`/shared-temp/jobs/<uuid>/media.part`. Файл переименовывается в `media.mp4` только
после успешного завершения потока.

До чтения проверяется `Content-Length`. Во время чтения ведётся независимый счётчик
байтов. При превышении `MAX_UPLOAD_MB` соединение закрывается, частичный файл
удаляется, а задача получает статус `too_large`. `Estimated-Content-Length` может
использоваться только для прогресса, но не как окончательная проверка.

Прогресс обновляется через существующий throttled callback не чаще настроенного
интервала. Файл никогда не собирается целиком в `bytes` или `bytearray`.

### 5.5 Метаданные TikTok

Cobalt не предоставляет стабильный отдельный metadata endpoint для TikTok. Поэтому
TikTok-задача создаётся без предварительного сетевого metadata-запроса:

- title до загрузки: `TikTok video`;
- canonical video ID берётся из URL;
- для короткой ссылки временный `source_media_id` формируется как стабильный SHA-256
  hash нормализованного URL с безопасным префиксом;
- после скачивания `ffprobe` определяет duration, width и height;
- безопасное имя Cobalt может обновить отображаемый title, если оно информативно.

YouTube metadata pipeline не меняется.

## 6. Пользовательский поток

```text
TikTok URL
  ↓ URL whitelist / subscription / rate limit
создание download_request и downloads
  ↓
очередь
  ↓
Cobalt prepare + stream
  ├─ успех → ffprobe → size check → Telegram
  └─ ошибка → удалить partial → yt-dlp
                              ├─ успех → ffprobe → Telegram
                              └─ ошибка → failed
  ↓
finally: удалить UUID job directory
```

Сообщения пользователю:

```text
⏳ Загрузка поставлена в очередь
⬇️ Скачиваю видео...
🔄 Основной способ недоступен, пробую резервный...
📤 Отправляю в Telegram...
✅ Готово
```

Если оба backend-а завершились ошибкой:

```text
❌ Не удалось получить видео из TikTok. Возможно, оно приватное,
удалено или временно ограничено платформой.
```

Внутренние Cobalt-коды, traceback, signed URL и cookie пользователю не показываются.

## 7. Ошибки и fallback

Fallback разрешён для:

- connect/read timeout Cobalt в пределах общего download timeout;
- HTTP 4xx/5xx Cobalt, кроме локально определённого превышения размера;
- ответа `error`;
- неподдерживаемого `local-processing`;
- некорректной JSON-схемы;
- сетевого обрыва до завершения файла;
- небезопасного URL в ответе Cobalt.

Fallback не выполняется для:

- отмены задачи или остановки приложения;
- исчерпания общего `DOWNLOAD_TIMEOUT_SECONDS`;
- превышения `MAX_UPLOAD_MB`;
- ошибки локальной файловой системы;
- ошибки Telegram после уже завершённой загрузки.

Общий timeout охватывает обе попытки, а не выделяется заново каждому backend-у.

## 8. SSRF и безопасность URL

Исходная ссылка проходит существующий строгий TikTok whitelist. Ответ Cobalt тоже
не считается доверенным.

Для tunnel URL разрешается только origin, совпадающий с настроенным
`COBALT_API_URL`. Для прямого URL разрешены только HTTP/HTTPS без credentials,
фрагмента и нестандартного порта. Перед каждым соединением и redirect hop выполняется
DNS resolution; loopback, link-local, multicast, unspecified и private адреса
запрещаются. Повторная проверка проводится после redirect, чтобы закрыть DNS rebinding
и переход на внутренний адрес.

Полный source URL, Cobalt tunnel URL, query tokens, cookies и HTTP authorization не
логируются. Логи содержат только source, безопасный media ID, backend, стадию и
нормализованный тип ошибки.

## 9. Docker Compose

Добавляется сервис `cobalt`:

- официальный образ `ghcr.io/imputnet/cobalt`, закреплённый версией и immutable
  digest;
- `API_URL=http://cobalt:9000/` и `API_PORT=9000`;
- только `expose: 9000`, без `ports`;
- healthcheck через внутренний `GET /`;
- та же backend network;
- `restart: unless-stopped`;
- без Docker socket и privileged mode;
- без Watchtower и автоматического обновления.

Бот не имеет жёсткого startup dependency на healthy Cobalt. Если сервис недоступен,
бот запускается, пишет безопасное предупреждение и продолжает обслуживать YouTube.

Cobalt не требует shared volume: поток идёт по внутренней сети прямо в файл,
созданный ботом на существующем `shared-temp`.

## 10. Конфигурация

В `Settings`, `.env.example` и локальный gitignored `.env` добавляются:

```env
COBALT_ENABLED=true
COBALT_API_URL=http://cobalt:9000
COBALT_REQUEST_TIMEOUT_SECONDS=30
COBALT_STREAM_TIMEOUT_SECONDS=1800
```

В local mode hostname должен быть Docker service name. URL нормализуется без
завершающего `/`. Timeout-ы валидируются Pydantic. Содержимое локального `.env` не
выводится и не коммитится.

## 11. PostgreSQL и админка

В `downloads` добавляется nullable поле `backend` со значениями `cobalt` и `ytdlp`.
Для успешно завершённой загрузки оно обязательно содержит фактический backend. Для
старых completed-записей миграция выставляет `ytdlp`; для неуспешных старых записей
значение может остаться `NULL`.

`/admin → Система` показывает:

- Cobalt включён/выключен;
- результат короткого healthcheck: доступен/недоступен;
- число активных и ожидающих загрузок остаётся существующим.

Healthcheck админки имеет короткий отдельный timeout и не влияет на очередь.

## 12. Cleanup и остановка

Внешний `try/finally` DownloadManager остаётся владельцем job-каталога. Каждый
backend отвечает только за закрытие своих процессов/HTTP response и удаление своего
partial-файла.

При SIGTERM/SIGINT:

- новые задачи не принимаются;
- активный Cobalt stream отменяется и закрывается;
- `yt-dlp` subprocess получает штатную остановку, затем kill при необходимости;
- download получает `cancelled`;
- job directory удаляется;
- HTTP session Cobalt закрывается до DB pool.

Startup и периодический stale cleanup остаются без изменений и удаляют забытые
каталоги по TTL, не затрагивая зарегистрированные активные job ID.

## 13. Лицензирование и обновления

Cobalt работает отдельным неизменённым AGPL-сервисом. README содержит ссылку на
исходный код, лицензию, закреплённую версию и инструкцию ручного обновления digest.
Код Cobalt не копируется в репозиторий бота.

Обновление выполняется осознанно: изменить tag/digest, проверить release notes,
выполнить Compose validation и optional integration test, после чего пользователь
сам пересоздаёт контейнеры.

## 14. Тестирование

Unit-тесты не требуют Docker, Telegram, TikTok или внешней сети. HTTP и subprocess
заменяются контролируемыми fake-реализациями.

Обязательные случаи:

- разбор ответов `tunnel`, `redirect`, `picker`, `error`, `local-processing`;
- отклонение неизвестного статуса и невалидного JSON;
- основной порядок `Cobalt → yt-dlp` для TikTok;
- отсутствие Cobalt в YouTube pipeline;
- удаление partial-файла до fallback;
- отсутствие fallback при cancel, общем timeout и too-large;
- ограничение размера по header и фактически записанным байтам;
- защита от localhost/private IP/credentials/DNS rebinding/опасного redirect;
- потоковая запись без накопления всего файла в памяти;
- cleanup после ошибки Cobalt, yt-dlp и Telegram;
- сохранение успешного backend-а в repository;
- миграция существующих строк;
- admin health status;
- валидация новых settings;
- `docker compose config`;
- secret scan и отсутствие опубликованных портов Cobalt.

Optional integration test запускается только при `RUN_INTEGRATION_TESTS=1` и наличии
`INTEGRATION_TIKTOK_URL`. Обычный `pytest` не обращается к TikTok или Cobalt.

## 15. Документация

README на русском языке описывает:

- зачем добавлен Cobalt;
- порядок primary/fallback;
- почему Cobalt не публикуется наружу;
- новые `.env` параметры;
- ресурсные требования;
- проверку `docker compose logs -f cobalt`;
- ручное обновление Cobalt;
- AGPL attribution;
- диагностику, когда оба backend-а не работают;
- будущую возможность подключения других платформ.

## 16. Приёмочные проверки

Работа считается завершённой, когда:

- TikTok route ставит задачу в очередь без обязательного metadata-вызова `yt-dlp`;
- Cobalt успешно создаёт `MediaPackage` из потоково записанного видео;
- искусственная ошибка Cobalt доказанно включает `yt-dlp` fallback;
- partial-файл не переживает fallback или исключение;
- YouTube regression suite проходит без изменения пользовательского поведения;
- PostgreSQL migration и repositories поддерживают поле `backend`;
- Cobalt не имеет опубликованного host port;
- локальный `.env` обновлён и остаётся gitignored;
- полный `pytest`, Ruff и `docker compose config` проходят;
- контейнеры не запускаются и не перезапускаются без отдельной просьбы пользователя.
