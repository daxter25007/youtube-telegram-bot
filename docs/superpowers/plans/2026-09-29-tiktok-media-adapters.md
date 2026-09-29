# План реализации TikTok и мультиплатформенных медиа-адаптеров

> **Для агентных исполнителей:** ОБЯЗАТЕЛЬНЫЙ НАВЫК: использовать
> `superpowers:executing-plans` для пошагового выполнения этого плана. Каждый шаг
> отмечается флажком `- [ ]`.

**Цель:** добавить безопасное скачивание публичных TikTok-видео и одновременно
перевести существующий YouTube-пайплайн на расширяемую архитектуру адаптеров.

**Архитектура:** реестр выбирает адаптер по строго проверенному URL, общий сервис
yt-dlp получает метаданные и скачивает доверенный вариант, а очередь, временное
хранилище и Telegram-отправка работают с универсальным `MediaPackage`. PostgreSQL
хранит источник, тип медиа и универсальный ключ варианта без потери YouTube-истории.

**Стек:** Python 3.12, aiogram 3.31, yt-dlp, ffmpeg/ffprobe, SQLAlchemy 2 async,
asyncpg, PostgreSQL, Alembic, pytest, Ruff, Docker Compose.

**Спецификация:**
`docs/superpowers/specs/2026-09-29-multi-platform-media-adapters-design.md`

## Общие ограничения

- Поддерживаются только видео YouTube и TikTok; фото и аудио пока только отражены в
  контрактах.
- TikTok принимает только HTTPS-ссылки на точных разрешённых hostname.
- Пользовательские данные не становятся аргументами yt-dlp, кроме одного URL после
  `--`; `shell=True` запрещён.
- YouTube сохраняет выбор 360p–2160p; TikTok использует доверенный `best_video`.
- Максимальный размер остаётся `MAX_UPLOAD_MB`, по умолчанию 1950 MB.
- Файлы живут только в job-каталоге и удаляются в `finally`.
- Local Bot API получает локальный `file://` URI; большие файлы не читаются в RAM.
- Unit-тесты не используют Telegram token, PostgreSQL, сеть или реальные загрузки.
- Интеграционный TikTok-тест запускается только по явной переменной окружения.
- Docker-контейнеры не запускаются и не перезапускаются без просьбы пользователя.
- Документация, сообщения пользователю и план ведутся на русском языке.

## Особое внимание при ревью

1. Короткая TikTok-ссылка может вести на фотопубликацию: она должна пройти URL-парсер,
   но быть отклонена после анализа метаданных.
2. TikTok может не сообщить размер или высоту: скачивание разрешается, но фактический
   размер всё равно проверяется до отправки.
3. Автоматическая постановка TikTok в очередь обязана соблюдать лимит одной загрузки
   на пользователя и корректно завершать запись при отказе очереди.
4. Миграция должна сохранить существующие YouTube-строки и корректно откатывать
   переименования столбцов.
5. Неожиданный путь, напечатанный yt-dlp, не должен позволить отправить файл вне
   job-каталога.

---

### Task 1: Доменные модели, безопасные URL и реестр адаптеров

**Файлы:**
- Создать: `app/services/media/__init__.py`
- Создать: `app/services/media/models.py`
- Создать: `app/services/media/adapters/base.py`
- Создать: `app/services/media/adapters/youtube.py`
- Создать: `app/services/media/adapters/tiktok.py`
- Создать: `app/services/media/registry.py`
- Изменить: `app/utils/urls.py`
- Создать: `tests/test_media_registry.py`
- Изменить: `tests/test_urls.py`

**Интерфейсы:**
- Создать `MediaSource(StrEnum)`, `MediaKind(StrEnum)`, `ParsedMediaUrl`,
  `DownloadOption`, `MediaMetadata`, `DownloadPolicy`, `MediaArtifact`, `MediaPackage`.
- Создать протокол `MediaAdapter` с методами `parse_url`, `parse_metadata`,
  `download_policy` и свойством `automatic_option`.
- Создать `MediaRegistry.parse(text: str) -> ParsedMediaUrl | None` и
  `MediaRegistry.adapter_for(source: MediaSource) -> MediaAdapter`.
- Сохранить `validate_youtube_url` как совместимую оболочку над реестром.

- [ ] Написать падающие тесты допустимых YouTube/TikTok URL, коротких TikTok URL,
  профилей, HTTP, credentials, портов, IP, localhost, управляющих символов и похожих
  доменов.
- [ ] Запустить только новые URL-тесты и подтвердить ожидаемое падение из-за отсутствия
  `MediaRegistry` и TikTok-адаптера.
- [ ] Реализовать минимальные доменные модели, адаптеры URL и реестр; канонический
  TikTok-путь — `/@<account>/video/<numeric-id>`, короткие ссылки — непустой путь
  только на `vm.tiktok.com` или `vt.tiktok.com`.
- [ ] Запустить URL-тесты и существующие тесты `tests/test_urls.py` до зелёного
  результата.
- [ ] Закоммитить изменения сообщением `feat: add media source registry`.

### Task 2: Универсальные метаданные и политики форматов

**Файлы:**
- Создать: `app/services/media/errors.py`
- Создать: `app/services/media/metadata.py`
- Создать: `app/services/media/formats.py`
- Изменить: `app/services/media/adapters/youtube.py`
- Изменить: `app/services/media/adapters/tiktok.py`
- Изменить: `app/services/youtube/formats.py`
- Создать: `tests/test_media_metadata.py`
- Изменить: `tests/test_formats.py`

**Интерфейсы:**
- Создать `MetadataError(kind: MetadataErrorKind)` и категории `unavailable`,
  `restricted`, `authentication`, `unsupported_media`, `extractor_changed`.
- Создать `YtDlpMetadataService.fetch(parsed: ParsedMediaUrl) -> MediaMetadata`.
- YouTube создаёт варианты `video_360`–`video_2160`; TikTok создаёт только
  `best_video` и возвращает `automatic_option="best_video"`.
- `DownloadPolicy` хранит только доверенный format selector и правила remux.

- [ ] Написать падающие тесты преобразования YouTube-фикстуры, TikTok-видео,
  TikTok-фотопоста, отсутствующего filesize/height, превышения оценочного лимита и
  классификации stderr.
- [ ] Запустить `tests/test_media_metadata.py` и подтвердить ожидаемое падение из-за
  отсутствия универсального сервиса.
- [ ] Реализовать общий subprocess метаданных и преобразование raw JSON в адаптерах;
  сохранить характеристический выбор MP4/H.264/AAC для YouTube, для TikTok выбирать
  лучший формат с видео и аудио с fallback на best video.
- [ ] Запустить тесты метаданных и форматов до зелёного результата.
- [ ] Закоммитить изменения сообщением `feat: add platform media metadata`.

### Task 3: Обобщение схемы PostgreSQL и репозиториев

**Файлы:**
- Создать: `alembic/versions/20260929_0002_generalize_media_sources.py`
- Изменить: `app/database/models/download.py`
- Изменить: `app/database/models/download_request.py`
- Изменить: `app/database/models/__init__.py`
- Изменить: `app/database/repositories/downloads.py`
- Изменить: `app/database/repositories/requests.py`
- Изменить: `app/services/statistics.py`
- Создать: `tests/test_media_repositories.py`
- Создать: `tests/test_media_migration.py`

**Интерфейсы:**
- `DownloadRepository.create` принимает `source`, `media_kind`, `source_media_id`,
  `source_url`, `requested_option` и необязательный `requested_quality`.
- `DownloadRequestRepository.create` принимает те же универсальные поля без качества.
- Модели хранят enum как строки с `native_enum=False`.
- Статистика качества исключает строки с `requested_quality IS NULL`.

- [ ] Написать падающие SQLite repository-тесты универсальных полей и тест миграции,
  проверяющий SQL/операции rename, backfill, nullable и downgrade.
- [ ] Запустить новые DB-тесты и подтвердить ожидаемое падение на старых сигнатурах и
  столбцах.
- [ ] Реализовать модели, репозитории, статистику и Alembic upgrade/downgrade с
  сохранением существующих YouTube-данных.
- [ ] Запустить DB-тесты до зелёного результата.
- [ ] Закоммитить изменения сообщением `feat: generalize media persistence`.

### Task 4: Общий загрузчик yt-dlp и безопасный `MediaPackage`

**Файлы:**
- Создать: `app/services/media/process.py`
- Создать: `app/services/media/downloader.py`
- Изменить: `app/services/youtube/process.py`
- Изменить: `app/services/youtube/downloader.py`
- Создать: `tests/test_media_downloader.py`

**Интерфейсы:**
- Создать `MediaDownloader.download(url, policy, job_dir, progress) -> MediaPackage`.
- Общий process helper предоставляет `subprocess_options()` и
  `stop_process(process)`.
- Старые YouTube-модули временно реэкспортируют общие сущности, чтобы переход не
  создавал циклических импортов и скрытых поломок.

- [ ] Написать падающие тесты списка аргументов, позиции `--`, отсутствия shell,
  прогресса, remux, отмены, ошибки процесса, ffprobe и отклонения пути вне job-каталога.
- [ ] Запустить тесты загрузчика и подтвердить ожидаемое падение из-за отсутствия
  `MediaDownloader`.
- [ ] Реализовать общий загрузчик и совместимые реэкспорты, не читая файл в память.
- [ ] Запустить тесты загрузчика до зелёного результата.
- [ ] Закоммитить изменения сообщением `refactor: share yt-dlp media downloader`.

### Task 5: Пакетная Telegram-отправка и жизненный цикл загрузки

**Файлы:**
- Изменить: `app/services/telegram_files.py`
- Изменить: `app/services/download_manager.py`
- Изменить: `tests/test_telegram_files.py`
- Создать: `tests/test_download_manager.py`

**Интерфейсы:**
- `TelegramFileService.send` принимает `MediaPackage`, но пока требует ровно один
  видеоартефакт и сохраняет fallback `sendVideo` → `sendDocument`.
- `DownloadManager.run` принимает `source_url`, `source`, `media_kind`, `option_key`
  и `DownloadPolicy` вместо YouTube-специфичных аргументов.

- [ ] Написать падающие тесты Local Bot API URI, одиночного видео, неподдерживаемого
  пакета, превышения размера, timeout, upload error, cancellation и cleanup в каждом
  исходе.
- [ ] Запустить целевые тесты и подтвердить падение на старых интерфейсах.
- [ ] Реализовать пакетную отправку и универсальный менеджер, сохранив статусы БД,
  увеличение счётчика пользователя и понятные русские ошибки.
- [ ] Запустить целевые тесты до зелёного результата.
- [ ] Закоммитить изменения сообщением `refactor: process media packages`.

### Task 6: Telegram-роутер YouTube и TikTok

**Файлы:**
- Изменить: `app/bot/routers/downloader.py`
- Изменить: `app/bot/keyboards/quality.py`
- Создать: `tests/test_downloader_router.py`

**Интерфейсы:**
- Входной обработчик использует `MediaRegistry`, а не `validate_youtube_url`.
- Callback имеет вид `media:<request_id>:<option_key>` и проверяет владельца, TTL и
  наличие ключа в серверных метаданных.
- Общий helper постановки задачи используется и callback YouTube, и автоматическим
  сценарием TikTok.

- [ ] Написать падающие router-тесты: неизвестный URL, подписка, rate limit,
  YouTube-кнопки, автоматический TikTok, фотопост, чужой/просроченный request,
  недоверенный option key, пользовательский лимит и закрытая очередь.
- [ ] Запустить тесты роутера и подтвердить падение на YouTube-специфичном коде.
- [ ] Реализовать общий поток, удалить request после успешной постановки, а при отказе
  очереди сохранить корректный статус и сообщение.
- [ ] Запустить тесты роутера до зелёного результата.
- [ ] Закоммитить изменения сообщением `feat: download TikTok videos`.

### Task 7: Wiring, приветствие, документация и итоговая проверка

**Файлы:**
- Изменить: `app/main.py`
- Изменить: `app/bot/routers/start.py`
- Изменить: `README.md`
- Изменить: `tests/integration/test_external_services.py`
- При необходимости изменить: `app/services/youtube/__init__.py`

**Интерфейсы:**
- `main.py` создаёт один реестр, универсальный metadata service и downloader и
  передаёт их через workflow data.
- `/start` честно сообщает о поддержке видео YouTube и TikTok.
- Integration test использует `INTEGRATION_TIKTOK_URL` только вместе с
  `RUN_INTEGRATION_TESTS=1`.

- [ ] Написать падающий wiring-тест либо расширить существующие тесты импорта и
  стартового текста до упоминания обеих платформ без обещания фото/аудио.
- [ ] Реализовать wiring, обновить приветствие и русскоязычный README: URL TikTok,
  ограничения фото, cookies, миграция и обновление yt-dlp.
- [ ] Запустить полный `pytest`; ожидается отсутствие падений и пропуск только явно
  отключённых интеграционных тестов.
- [ ] Запустить `ruff check .` и `ruff format --check .`; ожидается exit code 0.
- [ ] Проверить миграции командами Alembic без запуска постоянных контейнеров; если
  локальная PostgreSQL недоступна, явно зафиксировать непроверенный пункт.
- [ ] Запустить `docker compose config --quiet` без запуска сервисов; ожидается exit
  code 0.
- [ ] Проверить `rg -n "shell=True|os\.system|api\.telegram\.org" app` и убедиться,
  что опасные вызовы отсутствуют, а cloud URL не используется в local mode.
- [ ] Запустить Gitleaks с `--redact`, если бинарник установлен; секреты и `.env` не
  выводить.
- [ ] Закоммитить изменения сообщением `docs: document TikTok video support`.
