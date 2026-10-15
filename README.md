# YouTube + TikTok + Instagram + Pinterest → Telegram Bot

Готовый к эксплуатации Telegram-бот на Python 3.12, который проверяет подписку на обязательные каналы, получает метаданные YouTube, скачивает медиа через `yt-dlp`, self-hosted Cobalt и `ffmpeg`, а затем отправляет его через локальный Telegram Bot API Server. Для YouTube пользователь выбирает качество видео или M4A-аудио. Для TikTok, Instagram и Pinterest бот автоматически отправляет доступные видео, фото, GIF-анимации или карусели и, когда она есть, отдельную аудиодорожку.

> Используйте загрузку только для контента, который вы вправе скачивать и распространять. Учитывайте правила исходной площадки, Telegram и законодательство вашей страны.

## Архитектура

```text
Telegram user
    ↓
Telegram Local Bot API Server (--local, internal port 8081)
    ↓
aiogram bot → PostgreSQL (users, requests, channels, statistics, backend результата)
    ├─ YouTube → yt-dlp → ffmpeg merge/remux
    └─ TikTok / Instagram / Pinterest → Cobalt (основной) ─┐
                              └→ yt-dlp (fallback) ─┤
                                        ↓
/shared-temp/jobs/<uuid>/media.mp4
    ↓ shared Docker volume, local filesystem path
Telegram Local Bot API Server → Telegram user
```

Видео никогда не записываются в PostgreSQL. Контейнеры `bot` и `telegram-bot-api` видят один named volume как `/shared-temp`; Bot API получает локальный путь и читает файл напрямую, поэтому Python не загружает 1–2 GB в RAM. После успеха, ошибки, таймаута или отмены job-каталог удаляется в `finally`. При старте и затем каждый час удаляются неактивные каталоги старше `TEMP_FILE_TTL_HOURS`.

Сервисы Compose:

- `bot` — aiogram 3.31, async SQLAlchemy, yt-dlp, ffmpeg, поддерживаемый Node 22 runtime для YouTube EJS challenges;
- `cobalt` — отдельный self-hosted processing API для TikTok, Instagram и Pinterest, закреплённый tag+digest, доступный только внутри Docker network;
- `telegram-bot-api` — поддерживаемый образ `aiogram/telegram-bot-api`, собранный из официального `tdlib/telegram-bot-api`, режим local; multi-arch digest закреплён в Compose;
- `postgres` — PostgreSQL 17.

PostgreSQL, Local Bot API и Cobalt не публикуются на хост. Они доступны только внутри Docker network. Контейнер бота работает не от root. Недоступность Cobalt не блокирует запуск бота и YouTube: для TikTok, Instagram и Pinterest менеджер автоматически попробует yt-dlp в рамках того же общего таймаута.

## Возможности

- URL `youtube.com/watch`, `youtu.be`, `youtube.com/shorts` с жёстким whitelist хостов;
- публичные TikTok-видео, фотографии и карусели по каноническим ссылкам и официальным коротким ссылкам `vm.tiktok.com` / `vt.tiktok.com`;
- публичные Instagram Reels, одиночные публикации и смешанные фото/видео-карусели по ссылкам `/reel/`, `/reels/`, `/p/` и `/share/`;
- публичные Pinterest Pins и Stories по ссылкам `/pin/<id>` и официальным коротким ссылкам `pin.it`;
- Cobalt как основной TikTok/Instagram/Pinterest-backend с потоковой записью сразу на диск и yt-dlp как автоматический fallback;
- расширяемый реестр адаптеров для будущего добавления VK, RuTube и других источников без изменения очереди и временного хранилища;
- 144p, 240p, 360p, 480p, 720p, 1080p, 1440p, 2160p — только когда формат существует и оценочный размер не превышает лимит;
- отдельная кнопка YouTube `Только аудио` с результатом M4A;
- TikTok и Instagram-карусели отправляются по порядку альбомами до 10 фото/видео, а доступная аудиодорожка — отдельным сообщением;
- Telegram `file_id`-кэш для каждого видео, аудио и фото; повторный полный запрос не скачивает публикацию заново;
- предпочтение MP4/H.264 + M4A/AAC, fallback на лучший совместимый video/audio без hardcoded format ID;
- merge/remux через ffmpeg, `sendVideo` с `supports_streaming`, fallback на `sendDocument`;
- очередь, глобальная параллельность и лимит одной активной загрузки на пользователя;
- throttled progress, timeout и корректное завершение subprocess;
- обязательные каналы, Telegram-админка, статистика SQL;
- Alembic, pytest, ruff и secret scanning в GitHub Actions.

## Требования VPS

- Ubuntu 22.04/24.04 или современный Debian;
- Docker Engine и Docker Compose v2;
- достаточно диска под PostgreSQL, данные Bot API и одновременные временные видео;
- Telegram `api_id` и `api_hash` с [my.telegram.org → API development tools](https://core.telegram.org/api/obtaining_api_id);
- bot token от [@BotFather](https://t.me/BotFather).

Python, ffmpeg и yt-dlp на хост устанавливать не нужно.

## Первый запуск

```bash
git clone <repository-url>
cd youtube-telegram-bot
cp .env.example .env
nano .env
```

Обязательно замените `BOT_TOKEN`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `POSTGRES_PASSWORD` и пароль внутри `DATABASE_URL`. Добавьте числовые Telegram ID администраторов:

```dotenv
ADMIN_IDS=123456789,987654321
POSTGRES_PASSWORD=a-long-random-password
DATABASE_URL=postgresql+asyncpg://youtube_bot:a-long-random-password@postgres:5432/youtube_bot
```

Если пароль содержит `@`, `:`, `/`, `%` или другие специальные символы URL, percent-encode его в `DATABASE_URL`. Значение `POSTGRES_PASSWORD` при этом остаётся исходным паролем.

Username не используется для авторизации администратора.

### Перевод бота на Local Bot API

Официальная документация требует вызвать `logOut` на cloud Bot API до первого входа через локальный сервер; иначе получение updates не гарантируется. После успешного `logOut` локальный сервер доступен сразу, а возврат в cloud API заблокирован на 10 минут: [Telegram Bot API — logOut](https://core.telegram.org/bots/api#logout), [официальный сервер](https://github.com/tdlib/telegram-bot-api#moving-a-bot-to-a-local-server).

Утилита читает токен только из environment, просит явное подтверждение и не печатает его:

```bash
set -a
. ./.env
set +a
python3 scripts/migrate_to_local_api.py
```

На машине без Python можно выполнить одноразово в Python-контейнере после сборки:

```bash
docker compose build bot
docker compose run --rm --no-deps bot python scripts/migrate_to_local_api.py
```

Команда интерактивно попросит ввести `LOCAL`. Не передавайте token в CLI arguments и shell history.

После успешного `logOut`:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f bot
```

Эта команда запускает также внутренний сервис `cobalt`. Отдельные токены или публичный домен для него не нужны. Проверить его состояние можно командами:

```bash
docker compose ps cobalt
docker compose logs -f cobalt
```

Миграции Alembic автоматически применяются контейнером до запуска polling. Вручную:

```bash
docker compose run --rm bot alembic upgrade head
```

Логи Local Bot API:

```bash
docker compose logs -f telegram-bot-api
```

### Возврат к Telegram Cloud Bot API

Остановите bot, чтобы два API endpoint не получали updates одновременно. Вызовите `logOut` на старом локальном сервере согласно официальной процедуре переноса между серверами, затем остановите Local Bot API. После требуемого Telegram интервала задайте `TELEGRAM_API_MODE=cloud` и запустите bot снова. Не запускайте одновременно local и cloud polling. Если используется webhook, сначала удалите webhook и следуйте официальным описаниям методов [`deleteWebhook`, `close`, `logOut`](https://core.telegram.org/bots/api#available-methods).

```bash
docker compose stop bot
docker compose run --rm --no-deps bot python scripts/logout_local_api.py
docker compose stop telegram-bot-api
# затем TELEGRAM_API_MODE=cloud в .env; удалите local dependency из своего Compose override
# и запустите только bot+postgres
```

Проект не автоматизирует этот обратный переход: он требует осознанного управления endpoint и интервалом Telegram.

## Обязательные каналы и админка

1. Добавьте числовой Telegram ID в `ADMIN_IDS`, перезапустите bot.
2. Отправьте `/admin` → `📢 Каналы` → `➕ Добавить`.
3. Отправьте `@channel` или `-100xxxxxxxxxx`, проверьте карточку и нажмите `✅ Добавить`.

Для проверки подписки бот должен быть администратором канала. Для приватного канала ему также нужен доступ к invite link. Статусы `creator`, `administrator`, `member` считаются подпиской; `left` и `kicked` — нет. Изменения списка каналов начинают действовать немедленно.

Админка также показывает пользователей, последние загрузки, агрегаты за день/7/30 дней, объём, средний размер и время, популярные качества и TOP-10 пользователей.

## Размер файлов и временное хранилище

Local Bot API официально поддерживает upload до 2000 MB и локальные пути. Рабочий лимит по умолчанию — `MAX_UPLOAD_MB=1950`, чтобы оставить запас. До скачивания варианты с заведомо слишком большой оценкой скрываются; после скачивания обязательно проверяется фактический `stat().st_size`. Файл больше лимита не отправляется.

Один job хранится в `/shared-temp/jobs/<uuid>`. Название публикации не используется как путь. `shared-temp` монтируется read/write в bot и read-only в Bot API. Активные jobs защищены от periodic cleanup.

### Ограничения TikTok

Поддерживаются публичные видео, одиночные фотографии и фотокарусели. Для каждого TikTok-поста бот также запрашивает полную прикреплённую аудиодорожку в лучшем исходном формате Cobalt. MP3/M4A показываются в музыкальном плеере Telegram, остальные контейнеры отправляются документом без перекодирования.

TikTok сначала обрабатывает Cobalt. Каждая часть публикации скачивается потоково в отдельный временный файл, проверяется по заголовку и фактическому числу байтов, затем атомарно переименовывается. Redirect URL повторно проверяются: credentials, private/local IP, нестандартные внешние порты и опасные схемы запрещены. Если Cobalt временно недоступен для видео/аудио, каталог очищается и тот же job автоматически пробует yt-dlp. Фотопубликации требуют Cobalt. Ошибка одной части не отменяет отправку остальных доступных файлов.

### Ограничения Instagram

Поддерживаются только публичные Reels и публикации: одиночные фото/видео и смешанные карусели. Stories и ссылки на профили не принимаются. Cobalt является основным backend; `yt-dlp` используется как fallback для одиночного видео и аудио. Полное восстановление карусели требует Cobalt.

### Ограничения Pinterest

Поддерживаются публичные Pins и Stories с фото, GIF или видео. GIF отправляется как Telegram-анимация, а при отказе Telegram — документом. Ссылки на доски, профили и поиск не принимаются. Cobalt является основным backend; `yt-dlp` используется как fallback для видео. Качество одиночного изображения зависит от варианта, возвращённого текущей версией Cobalt.

Элементы карусели получают независимые ключи, сохраняются и отправляются в исходном порядке. Если Cobalt предоставляет отдельную аудиодорожку, она отправляется после визуальных частей; отсутствие аудио не отменяет доставку доступных фото и видео.

Для публикаций, требующих входа, yt-dlp можно позднее снабдить runtime cookies описанным ниже способом; cookies никогда не добавляются в Git или Docker image. Cobalt в текущей конфигурации рассчитан на публичные видео.

## Cookies (опционально)

По умолчанию cookies не нужны. Если серверу позже потребуется авторизация, сохраните файл только на VPS, выставьте `YTDLP_COOKIES_FILE=/run/secrets/ytdlp_cookies` и подключите его read-only runtime volume к `bot`:

```yaml
services:
  bot:
    volumes:
      - ./secrets/cookies.txt:/run/secrets/ytdlp_cookies:ro
```

`cookies.txt` и `*.cookies` игнорируются Git и не копируются Docker build context. Никогда не добавляйте cookies в Dockerfile.

## Конфигурация

Главные параметры описаны в `.env.example`:

- `MAX_CONCURRENT_DOWNLOADS=3`, `MAX_DOWNLOADS_PER_USER=1`;
- `DOWNLOAD_TIMEOUT_SECONDS=1800`;
- `TEMP_FILE_TTL_HOURS=6`, `CLEANUP_INTERVAL_SECONDS=3600`;
- `RATE_LIMIT_URLS=5`, `RATE_LIMIT_WINDOW_SECONDS=60`;
- `DOWNLOAD_REQUEST_TTL_MINUTES=30`;
- `PROGRESS_UPDATE_SECONDS=3`.
- `COBALT_ENABLED=true` — включить Cobalt как основной TikTok/Instagram/Pinterest-backend;
- `COBALT_API_URL=http://cobalt:9000` — только внутренний origin Compose-сервиса;
- `COBALT_REQUEST_TIMEOUT_SECONDS=30` — timeout получения download instruction;
- `COBALT_STREAM_TIMEOUT_SECONDS=1800` — timeout потокового ответа Cobalt.

`TELEGRAM_LOCAL_API_URL` должен оставаться `http://telegram-bot-api:8081` в Docker. В local mode конфигурация отвергает `api.telegram.org`. Токен, API hash и DSN маскируются в логах; environment целиком не логируется.

## Разработка и проверки

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
ruff check .
ruff format --check .
pytest
```

Unit tests не требуют token, сети социальных площадок, PostgreSQL или Local Bot API. Внешние integration tests помечены `integration` и пропускаются, пока явно не задано:

```bash
RUN_INTEGRATION_TESTS=1 pytest -m integration
```

Для integration suite экспортируйте `DATABASE_URL`, `TELEGRAM_LOCAL_API_URL`, `BOT_TOKEN`; сетевые проверки дополнительно запускаются при наличии `INTEGRATION_YOUTUBE_URL` и/или `INTEGRATION_TIKTOK_URL`. Проверка Cobalt использует `COBALT_API_URL` и не выводит исходный или tunnel URL.

Проверка Compose:

```bash
cp .env.example .env
docker compose config --quiet
```

GitHub Actions запускает ruff, pytest и Gitleaks. Локально при установленном Gitleaks:

```bash
gitleaks detect --source . --no-banner --redact
```

## Обновление

Приложение:

```bash
git pull --ff-only
docker compose build --pull bot
docker compose up -d
docker compose logs -f bot
```

`yt-dlp` закреплён нижней границей в `pyproject.toml` и устанавливается при сборке. Для обновления пересоберите без pip cache:

```bash
docker compose build --no-cache bot
docker compose up -d bot
```

Образ Local Bot API закреплён digest-ом. Для обновления сначала получите новый digest проверенного multi-arch образа, замените его в `docker-compose.yml`, затем:

```bash
docker buildx imagetools inspect aiogram/telegram-bot-api:latest
docker compose pull telegram-bot-api
docker compose up -d telegram-bot-api bot
```

Образ Cobalt также закреплён точным tag и multi-arch digest. Перед обновлением проверьте [официальный пакет](https://github.com/imputnet/cobalt/pkgs/container/cobalt) и [заметки о версиях](https://github.com/imputnet/cobalt/discussions/categories/announcements), замените tag+digest и примените обновление:

```bash
docker compose pull cobalt
docker compose up -d cobalt
docker compose logs -f cobalt
```

Cobalt распространяется по AGPL-3.0; исходный код и лицензия доступны в [официальном репозитории imputnet/cobalt](https://github.com/imputnet/cobalt). В проект не включён публичный Cobalt instance и не используется чужой публичный API.

## Backup PostgreSQL

Создать backup без публикации порта БД:

```bash
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > youtube_bot.dump
```

Восстановление выполняйте в остановленное приложение и предварительно проверьте backup:

```bash
docker compose stop bot
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < youtube_bot.dump
docker compose start bot
```

Named volumes `telegram-bot-api-data` и `postgres-data` не удаляйте командой `docker compose down -v`, если данные нужны.

## Безопасность

- `.env`, cookies, базы, логи и temp-файлы исключены из Git;
- callback содержит только короткий random request ID и серверный option key, никогда полный URL или аргументы yt-dlp;
- ownership и TTL запроса проверяются на callback;
- URL проверяется адаптером конкретной площадки; private/local hosts, похожие домены, credentials, нестандартные порты и другие схемы отвергаются;
- пользовательский ввод никогда не попадает в shell: subprocess запускаются списком аргументов;
- title очищается и используется только как display filename;
- PostgreSQL, Local Bot API и Cobalt не имеют host `ports`;
- Cobalt работает read-only с `no-new-privileges` и без Docker socket;
- секреты не передаются в CLI arguments приложения и маскируются formatter-ом логов.

## Структура

```text
app/
  bot/{routers,keyboards,middlewares,states}/
  database/{models,repositories}/
  services/cobalt/{client,models,security}.py
  services/media/{adapters,backends,metadata,downloader,models,probe,registry}.py
  services/youtube/  # совместимые YouTube format helpers
  services/{cleanup,download_manager,queue,statistics,subscriptions,telegram_files}.py
  utils/
  config.py
  main.py
alembic/versions/
scripts/{start,migrate_to_local_api,logout_local_api}.py
tests/
Dockerfile
docker-compose.yml
pyproject.toml
```
