# Cobalt TikTok Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить self-hosted Cobalt как основной backend загрузки TikTok-видео с автоматическим fallback на существующий yt-dlp.

**Architecture:** Отделить маршрутизацию backend-ов от source adapters и DownloadManager. TikTok использует цепочку `CobaltBackend → YtDlpBackend`, YouTube — только `YtDlpBackend`; Cobalt потоково пишет файл в существующий UUID job-каталог, а общий manager сохраняет прежние timeout, size check, Telegram upload и cleanup.

**Tech Stack:** Python 3.12, aiogram 3, aiohttp, yt-dlp, ffmpeg/ffprobe, SQLAlchemy async, PostgreSQL, Alembic, Docker Compose, Cobalt API, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-30-cobalt-tiktok-fallback-design.md`

## Global Constraints

- Текущий релиз включает только публичные TikTok-видео; фото, аудио и новые площадки не включать.
- Cobalt доступен только внутри Docker network, без host `ports`, privileged mode и Docker socket.
- Не использовать сторонний публичный Cobalt API, shell=True или пользовательские CLI-аргументы.
- Видео писать потоково в `/shared-temp/jobs/<uuid>`; не накапливать файл в RAM.
- `MAX_UPLOAD_MB` проверять по header и фактически записанным байтам.
- Общий `DOWNLOAD_TIMEOUT_SECONDS` охватывает Cobalt и yt-dlp вместе.
- Cancel, общий timeout, too-large и filesystem error не запускают fallback.
- YouTube metadata/quality flow и Local Bot API filesystem upload не менять.
- Локальный `.env` обновить, но не читать целиком, не логировать и не коммитить.
- Документацию вести на русском языке.
- Не запускать и не перезапускать контейнеры без отдельной просьбы пользователя.

## Review Focus

- Cobalt возвращает URL своего tunnel с неожиданным hostname: запрос должен быть отклонён до соединения (`Task 2`).
- HTTP redirect меняет публичный host на private/loopback IP: каждый hop должен быть повторно проверен (`Task 2`).
- `Content-Length` отсутствует или занижен: фактический счётчик должен остановить поток на лимите (`Task 3`).
- Cobalt успел создать partial-файл и оборвал поток: файл должен исчезнуть до yt-dlp fallback (`Task 3/4`).
- Cobalt выключен или не запущен: бот и YouTube должны запускаться и работать, TikTok должен перейти на yt-dlp (`Task 5`).

---

### Task 1: Backend domain и маршрутизация

**Files:**
- Create: `app/services/media/backends/base.py`
- Create: `app/services/media/backends/router.py`
- Create: `app/services/media/backends/ytdlp.py`
- Create: `app/services/media/backends/__init__.py`
- Modify: `app/services/media/models.py`
- Test: `tests/test_media_backend_router.py`

**Interfaces:**
- Consumes: существующие `MediaDownloader.download(...)`, `DownloadPolicy`, `MediaPackage`, `ProgressCallback`.
- Produces: `DownloadBackend`, `BackendErrorKind`, `BackendError`, `MediaDownloadRequest`, `MediaBackend`, `MediaBackendRouter.download(...) -> BackendResult`, `YtDlpBackend`.

- [ ] **Step 1: Write failing router tests**

Проверить порядок TikTok backends, отсутствие Cobalt в YouTube, retry после `BackendErrorKind.RETRYABLE`, очистку partial-файлов перед retry и отсутствие retry для `TOO_LARGE`, `CANCELLED`, `FILESYSTEM`.

- [ ] **Step 2: Run tests to verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests\test_media_backend_router.py -q`  
Expected: FAIL из-за отсутствующих backend classes.

- [ ] **Step 3: Implement domain and router**

Точные сигнатуры:

```python
class DownloadBackend(StrEnum):
    YTDLP = "ytdlp"
    COBALT = "cobalt"

@dataclass(frozen=True, slots=True)
class MediaDownloadRequest:
    source: MediaSource
    url: str
    option_key: str
    policy: DownloadPolicy

@dataclass(frozen=True, slots=True)
class BackendResult:
    package: MediaPackage
    backend: DownloadBackend

class MediaBackend(Protocol):
    name: DownloadBackend
    async def download(request, job_dir, progress) -> MediaPackage: ...

class MediaBackendRouter:
    async def download(request, job_dir, progress, on_fallback) -> BackendResult: ...
```

Router получает immutable mapping `MediaSource -> tuple[MediaBackend, ...]`. Очистка перед retry разрешена только внутри resolved job directory.

- [ ] **Step 4: Run backend router tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_media_backend_router.py tests\test_media_downloader.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```text
feat: add media backend routing
```

### Task 2: Cobalt API client и SSRF-защита

**Files:**
- Create: `app/services/cobalt/__init__.py`
- Create: `app/services/cobalt/models.py`
- Create: `app/services/cobalt/security.py`
- Create: `app/services/cobalt/client.py`
- Modify: `app/config.py`
- Test: `tests/test_cobalt_client.py`
- Test: `tests/test_cobalt_security.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `Settings`, `MediaSource`, aiohttp-compatible injected session/resolver.
- Produces: `CobaltResponse`, `CobaltResponseKind`, `CobaltClient.prepare(url)`, `CobaltClient.healthcheck()`, `SafeRemoteUrlValidator.validate(url, allowed_internal_origin=None)`.

- [ ] **Step 1: Write failing config, parser and security tests**

Покрыть `tunnel`, `redirect`, video picker, photo-only picker, `error`, `local-processing`, unknown status, invalid JSON, credentials, localhost, IPv4/IPv6 private ranges, unsafe port, internal tunnel origin и redirect на private IP.

- [ ] **Step 2: Run tests to verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests\test_cobalt_client.py tests\test_cobalt_security.py tests\test_config.py -q`  
Expected: FAIL из-за отсутствующего Cobalt client/settings.

- [ ] **Step 3: Implement settings and typed Cobalt client**

Добавить settings с точными default-значениями:

```text
cobalt_enabled=True
cobalt_api_url="http://cobalt:9000"
cobalt_request_timeout_seconds=30
cobalt_stream_timeout_seconds=1800
```

`prepare()` отправляет body из спецификации, проверяет HTTP/JSON schema и не включает URL/tokens в exception text. `healthcheck()` использует отдельный короткий timeout и возвращает bool.

- [ ] **Step 4: Implement URL validator**

Resolver передаётся зависимостью для unit-тестов. Проверять scheme, credentials, fragment, port, resolved IP всех адресов и каждый redirect hop. Tunnel допускает только exact origin настроенного Cobalt.

- [ ] **Step 5: Run client/security tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_cobalt_client.py tests\test_cobalt_security.py tests\test_config.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```text
feat: add secure Cobalt API client
```

### Task 3: Потоковый Cobalt backend

**Files:**
- Create: `app/services/media/probe.py`
- Create: `app/services/media/backends/cobalt.py`
- Modify: `app/services/media/downloader.py`
- Test: `tests/test_cobalt_backend.py`
- Modify: `tests/test_media_downloader.py`

**Interfaces:**
- Consumes: `CobaltClient.prepare`, `SafeRemoteUrlValidator`, `MediaDownloadRequest`, `max_upload_bytes`, `ProgressCallback`.
- Produces: `CobaltBackend.download(...) -> MediaPackage`, `probe_media(path) -> MediaProbe` shared with yt-dlp.

- [ ] **Step 1: Write failing streaming tests**

Покрыть chunked write, `.part -> .mp4`, progress, Content-Length over limit, missing/underreported length, mid-stream exception cleanup, cancellation cleanup, safe tunnel и unsafe redirect.

- [ ] **Step 2: Run tests to verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests\test_cobalt_backend.py -q`  
Expected: FAIL из-за отсутствующего CobaltBackend.

- [ ] **Step 3: Extract shared ffprobe helper**

Перенести существующую `_probe` без изменения поведения в `probe_media(path: Path) -> MediaProbe`; сохранить cancellation/timeout semantics.

- [ ] **Step 4: Implement CobaltBackend**

Писать фиксированными chunks в `media.part`, проверять directory containment, header и реальный размер, закрывать response при любом выходе, выполнять atomic rename и `probe_media` только после полного потока.

- [ ] **Step 5: Run downloader tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_cobalt_backend.py tests\test_media_downloader.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```text
feat: stream TikTok media through Cobalt
```

### Task 4: TikTok queue flow и automatic fallback

**Files:**
- Modify: `app/services/media/adapters/tiktok.py`
- Modify: `app/bot/routers/downloader.py`
- Modify: `app/services/download_manager.py`
- Modify: `app/main.py`
- Test: `tests/test_downloader_router.py`
- Test: `tests/test_download_manager.py`

**Interfaces:**
- Consumes: `MediaBackendRouter`, `BackendResult`, existing queue/request repositories.
- Produces: TikTok placeholder metadata builder; DownloadManager использует backend router и сохраняет backend result.

- [ ] **Step 1: Write failing TikTok flow tests**

Доказать, что TikTok не вызывает yt-dlp metadata, сразу создаёт request/download и queue job; canonical ID сохраняется, short URL получает стабильный hash ID; YouTube продолжает вызывать metadata и показывать кнопки.

- [ ] **Step 2: Write failing manager fallback tests**

Проверить сообщение fallback, единый общий timeout, успешную отправку результата Cobalt, user-friendly ошибку при отказе обоих backend-ов и cleanup после каждого исхода.

- [ ] **Step 3: Run tests to verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests\test_downloader_router.py tests\test_download_manager.py -q`  
Expected: FAIL на прежнем TikTok metadata flow и прямом MediaDownloader.

- [ ] **Step 4: Implement placeholder metadata and route split**

`TikTokAdapter.initial_metadata(parsed) -> MediaMetadata` создаёт `best_video`, title `TikTok video`, duration/height unknown и stable ID по спецификации. YouTube path остаётся прежним.

- [ ] **Step 5: Integrate backend router into DownloadManager/main**

Manager создаёт `MediaDownloadRequest`, вызывает router внутри существующего `asyncio.timeout`, отображает fallback status и использует `BackendResult.package`. Cobalt session закрывается при shutdown.

- [ ] **Step 6: Run flow tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_downloader_router.py tests\test_download_manager.py tests\test_cleanup.py tests\test_queue.py -q`  
Expected: PASS.

- [ ] **Step 7: Commit**

```text
feat: route TikTok downloads through Cobalt fallback
```

### Task 5: Persistence, admin status и Docker service

**Files:**
- Modify: `app/database/models/download.py`
- Modify: `app/database/repositories/downloads.py`
- Create: `alembic/versions/20260930_0003_add_download_backend.py`
- Modify: `app/bot/routers/admin.py`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `.env` (gitignored, значения не выводить)
- Test: `tests/test_media_repositories.py`
- Create: `tests/test_cobalt_migration.py`
- Modify: `tests/test_admin_download_display.py`
- Create: `tests/test_compose_security.py`

**Interfaces:**
- Consumes: `DownloadBackend`, `CobaltClient.healthcheck()`.
- Produces: `Download.backend`, repository status update with backend, `/admin → Система` Cobalt status, internal `cobalt` Compose service.

- [ ] **Step 1: Write failing migration/repository/admin/compose tests**

Проверить nullable backend, backfill только completed rows, сохранение successful backend, admin text, отсутствие `ports`, privileged и Docker socket, pinned digest, healthcheck.

- [ ] **Step 2: Run tests to verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests\test_media_repositories.py tests\test_cobalt_migration.py tests\test_admin_download_display.py tests\test_compose_security.py -q`  
Expected: FAIL из-за отсутствующего поля/service.

- [ ] **Step 3: Implement model, migration and repository**

Поле `backend` использует non-native enum/string, nullable. Upgrade backfill: `completed -> ytdlp`; downgrade удаляет поле без изменения остальных данных.

- [ ] **Step 4: Implement admin health and Compose service**

Cobalt image закрепить tag+digest, добавить internal expose/healthcheck/restart, но не делать Cobalt обязательным условием старта bot service.

- [ ] **Step 5: Update env files safely**

Добавить четыре `COBALT_*` значения в `.env.example` и gitignored `.env` без чтения/печати остальных значений. Подтвердить `git ls-files .env` возвращает пусто.

- [ ] **Step 6: Run persistence and Compose tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_media_repositories.py tests\test_cobalt_migration.py tests\test_admin_download_display.py tests\test_compose_security.py -q`  
Expected: PASS.

- [ ] **Step 7: Commit tracked files**

```text
feat: deploy and observe internal Cobalt service
```

### Task 6: Русская документация и итоговая проверка

**Files:**
- Modify: `README.md`
- Modify: `tests/integration/test_media_metadata_live.py` или создать отдельный Cobalt integration test рядом
- Modify: `.github/workflows/ci.yml` только если Compose security test требует системной зависимости

**Interfaces:**
- Consumes: завершённые settings, Compose service и backend behavior.
- Produces: полная инструкция запуска/обновления/диагностики Cobalt и optional live test.

- [ ] **Step 1: Add optional integration coverage**

Test пропускается без `RUN_INTEGRATION_TESTS=1` и `INTEGRATION_TIKTOK_URL`; он не должен логировать URL или tunnel token.

- [ ] **Step 2: Update Russian README**

Описать architecture, primary/fallback, новые env-параметры, Cobalt logs, отсутствие host port, resource note, manual update, AGPL source/license link и диагностику отказа обоих backend-ов.

- [ ] **Step 3: Run full verification**

Run:

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
docker compose config --quiet
.venv\Scripts\alembic.exe heads
```

Expected: tests PASS (integration skipped), Ruff clean, Compose exit 0, single Alembic head `20260930_0003`.

- [ ] **Step 4: Security review**

Проверить отсутствие tracked `.env`, hardcoded secrets, `shell=True`, опасных URL переходов, host port Cobalt и логирования tunnel/cookie/token.

- [ ] **Step 5: Commit**

```text
docs: document resilient TikTok downloads with Cobalt
```
