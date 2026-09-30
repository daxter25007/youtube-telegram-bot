# YouTube Audio and TikTok Media Bundles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add YouTube M4A downloads and automatic TikTok delivery of video/photos plus attached audio, with per-part Telegram caching and partial-failure handling.

**Architecture:** Generalize the media pipeline around an ordered publication manifest and prepared media parts. Backends discover parts before streaming, the manager resolves each part against Telegram cache, and the Telegram service delivers local and cached video, audio, and ordered photo albums through one package-aware boundary.

**Tech Stack:** Python 3.12, aiogram 3.31, SQLAlchemy 2 async, Alembic, aiohttp, yt-dlp, pytest/pytest-asyncio, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-30-youtube-audio-tiktok-bundles-design.md`

## Global Constraints

- YouTube audio output is M4A and is selected with a full-width `🎵 Только аудио` keyboard row.
- TikTok has no selection screen: every post sends all visual content first and attached full audio second.
- Preserve TikTok's audio container; use Telegram audio playback only for MP3/M4A and document delivery otherwise.
- Preserve carousel order and split Telegram albums into groups of at most ten photos.
- Cache every video, audio, and photo independently; a complete repeated publication must not contact Cobalt or download media.
- Retry only missing parts after a partial result; one failed part must not suppress successful parts.
- Keep the existing SSRF validation, queue/subscription limits, file-size limit, and temporary-directory cleanup guarantees.
- Add no new runtime dependency.

## Review Focus

- A picker containing one unsafe URL among otherwise valid items must fetch none of the unsafe item and must not weaken validation for the rest; Task 2 adds this test.
- A carousel with 11 or more photos must retain exact order across multiple Telegram albums; Task 5 adds this test.
- One expired cached photo in a carousel must invalidate only that part and rebuild the ordered visual set; Task 6 adds this test.
- One oversized TikTok part must not prevent smaller valid parts from being delivered and cached; Tasks 4 and 6 add this test.
- An audio container other than MP3/M4A must be sent and resent as a document without renaming it; Task 5 adds this test.

---

### Task 1: YouTube M4A option and quality keyboard

**Files:**
- Modify: `app/services/media/models.py`
- Modify: `app/services/media/downloader.py`
- Modify: `app/services/media/adapters/youtube.py`
- Modify: `app/bot/keyboards/quality.py`
- Modify: `app/bot/routers/downloader.py`
- Modify: `app/services/youtube/formats.py`
- Modify: `tests/test_media_metadata.py`
- Modify: `tests/test_media_downloader.py`
- Modify: `tests/test_downloader_router.py`
- Modify: `tests/test_formats.py`

**Interfaces:**
- Produces: `DownloadPolicy.media_kind: MediaKind`, `DownloadPolicy.extract_audio: bool`, and `DownloadPolicy.audio_format: str | None`.
- Produces: YouTube option key `audio_m4a` with `MediaKind.AUDIO`.
- Produces: `MediaArtifact.part_key`, defaulting to the selected option key at the backend boundary.

- [ ] **Step 1: Write failing adapter, command, keyboard, and router tests**

Add tests asserting that YouTube metadata appends `audio_m4a`; its label is `🎵 Только аудио`; the final keyboard row contains only that option; cached audio receives `⚡`; the policy builds yt-dlp audio extraction arguments ending in M4A; and `_enqueue_option` records/passes `option.media_kind` instead of the request's original video kind.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.\.venv\Scripts\python -m pytest tests/test_media_metadata.py tests/test_media_downloader.py tests/test_downloader_router.py tests/test_formats.py -q`

Expected: failures for the absent `audio_m4a` option/audio policy and the keyboard/router still treating every option as video. Existing expanded-quality assertions should continue to pass.

- [ ] **Step 3: Implement the audio policy and UI**

Extend `DownloadPolicy` with video-compatible defaults, make `build_download_command()` choose either video merge/remux arguments or `--extract-audio --audio-format m4a`, emit `MediaKind.AUDIO` artifacts for audio policies, append `audio_m4a` in `YouTubeAdapter.parse_metadata()`, accept it in `download_policy()`, and group only video buttons two per row before the single audio row. Change enqueueing, cache lookup, and download creation to use `option.media_kind`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `.\.venv\Scripts\python -m pytest tests/test_media_metadata.py tests/test_media_downloader.py tests/test_downloader_router.py tests/test_formats.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the YouTube audio and already-approved quality-grid changes**

```powershell
git add app/services/media/models.py app/services/media/downloader.py app/services/media/adapters/youtube.py app/bot/keyboards/quality.py app/bot/routers/downloader.py app/services/youtube/formats.py tests/test_media_metadata.py tests/test_media_downloader.py tests/test_downloader_router.py tests/test_formats.py docs/superpowers/plans/2026-09-30-youtube-audio-tiktok-media-bundles.md
git commit -m "feat: add YouTube audio download option"
```

### Task 2: Normalize all Cobalt publication items

**Files:**
- Modify: `app/services/cobalt/models.py`
- Modify: `app/services/cobalt/client.py`
- Modify: `tests/test_cobalt_client.py`

**Interfaces:**
- Consumes: `MediaKind` and stable part keys from Task 1.
- Produces: `CobaltMode.AUTO` and `CobaltMode.AUDIO`.
- Produces: `CobaltPreparedItem(part_key: str, media_kind: MediaKind, position: int, url: str, filename: str, response_kind: CobaltResponseKind, internal_origin: str | None)`.
- Produces: `CobaltMediaResponse(items: tuple[CobaltPreparedItem, ...])`.
- Produces: `CobaltClient.prepare(source_url: str, *, mode: CobaltMode, tiktok_full_audio: bool = False) -> CobaltMediaResponse`.

- [ ] **Step 1: Write failing Cobalt request and response tests**

Cover auto-mode video tunnel/redirect responses, ordered photo picker entries named `photo_000...`, picker video, picker-provided audio, explicit audio mode with `audioFormat: "best"` and `tiktokFullAudio: true`, filename preservation, malformed entries, and validation of every supported URL. Add the Review Focus case where one picker URL is unsafe and assert that URL is rejected before it can be fetched.

- [ ] **Step 2: Run the client tests and verify RED**

Run: `.\.venv\Scripts\python -m pytest tests/test_cobalt_client.py -q`

Expected: failures because the client returns one selected video and cannot configure audio mode.

- [ ] **Step 3: Implement collection parsing and mode-aware request bodies**

Replace the scalar response fields with ordered prepared items, derive media kind and stable part keys from response type/mode, preserve all supported picker entries, use picker-level audio when present, and run the existing validator for every returned URL with the correct tunnel origin.

- [ ] **Step 4: Run the client tests and verify GREEN**

Run: `.\.venv\Scripts\python -m pytest tests/test_cobalt_client.py -q`

Expected: all client tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/services/cobalt/models.py app/services/cobalt/client.py tests/test_cobalt_client.py
git commit -m "feat: parse Cobalt media collections"
```

### Task 3: Persist publication manifests and query part caches

**Files:**
- Create: `app/database/models/media_manifest.py`
- Create: `app/database/repositories/media_manifests.py`
- Create: `alembic/versions/20260930_0005_add_media_manifests.py`
- Modify: `app/database/models/__init__.py`
- Modify: `app/database/repositories/media_cache.py`
- Modify: `app/services/media/models.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_media_repositories.py`

**Interfaces:**
- Produces: `MediaPart(key: str, media_kind: MediaKind, position: int, filename: str | None = None, mime_type: str | None = None)` with JSON serialization helpers.
- Produces: `MediaManifestRepository.get(source, source_media_id)` and `upsert(source, source_media_id, parts)`.
- Produces: `MediaCacheRepository.list_for_media(source, source_media_id)` and `get_many(source, source_media_id, identities)` where identity is `(MediaKind, part_key)`.

- [ ] **Step 1: Write failing model/repository tests**

Test ordered manifest round-tripping, upsert replacement, unique identity behavior on SQLite, listing mixed video/audio/photo cache rows, and selecting only requested missing/cached part identities.

- [ ] **Step 2: Run repository tests and verify RED**

Run: `.\.venv\Scripts\python -m pytest tests/test_database.py tests/test_media_repositories.py -q`

Expected: import/model failures for the missing manifest and repository methods.

- [ ] **Step 3: Implement the model, repository, exports, and migration**

Create `media_manifests` with integer primary key, source, source media ID, JSON ordered parts, timestamps, and unique `(source, source_media_id)`. Implement PostgreSQL/SQLite upsert in the same style as `MediaCacheRepository.store()` and non-destructive downgrade behavior.

- [ ] **Step 4: Run repository tests and verify GREEN**

Run: `.\.venv\Scripts\python -m pytest tests/test_database.py tests/test_media_repositories.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Verify migration topology and commit**

Run: `.\.venv\Scripts\python -m alembic heads`

Expected: one head, `20260930_0005`.

```powershell
git add app/database/models/media_manifest.py app/database/repositories/media_manifests.py alembic/versions/20260930_0005_add_media_manifests.py app/database/models/__init__.py app/database/repositories/media_cache.py app/services/media/models.py tests/test_database.py tests/test_media_repositories.py
git commit -m "feat: persist media publication manifests"
```

### Task 4: Prepare and download multi-part backend packages

**Files:**
- Modify: `app/services/media/backends/base.py`
- Modify: `app/services/media/backends/router.py`
- Modify: `app/services/media/backends/ytdlp.py`
- Modify: `app/services/media/backends/cobalt.py`
- Modify: `app/services/media/downloader.py`
- Modify: `app/services/media/models.py`
- Modify: `tests/test_media_backend_router.py`
- Modify: `tests/test_cobalt_backend.py`
- Modify: `tests/test_media_downloader.py`

**Interfaces:**
- Produces: `PreparedMedia(request: MediaDownloadRequest, parts: tuple[MediaPart, ...], backend_payload: object | None)`.
- Produces: `PreparedCallback = Callable[[PreparedMedia], Awaitable[frozenset[str]]]`; returned keys are the parts the backend must download.
- Produces: backend `prepare(request) -> PreparedMedia` and `download_prepared(prepared, include_keys, job_dir, progress) -> MediaPackage`.
- Produces: `MediaPartFailure(part_key: str, media_kind: MediaKind, error_kind: BackendErrorKind)` and `MediaPackage.failures`.

- [ ] **Step 1: Write failing preparation, selective-download, and partial-result tests**

Assert that Cobalt preparation combines auto visual items with one audio request unless picker audio already exists; selected part keys alone are streamed; each file uses a collision-free part path and preserves extension/MIME; per-file size limits create a failure entry while smaller items survive; unsafe redirects remain rejected; and yt-dlp fallback produces video plus original-format TikTok audio while YouTube audio stays M4A.

- [ ] **Step 2: Run backend tests and verify RED**

Run: `.\.venv\Scripts\python -m pytest tests/test_media_backend_router.py tests/test_cobalt_backend.py tests/test_media_downloader.py -q`

Expected: failures for the absent preparation contract, selective parts, and partial package results.

- [ ] **Step 3: Implement prepared backend routing**

Split discovery from streaming in the backend protocol and router. Have the router invoke the callback after each backend prepares, then download only returned keys. Cobalt prepares visuals and full attached audio, downloads parts independently, probes video metadata only where applicable, and records per-part failures. Extend yt-dlp command execution so its declared media kind/part key drives artifact metadata and TikTok fallback can attempt both video and audio.

- [ ] **Step 4: Run backend tests and verify GREEN**

Run: `.\.venv\Scripts\python -m pytest tests/test_media_backend_router.py tests/test_cobalt_backend.py tests/test_media_downloader.py -q`

Expected: all selected tests pass, including oversized-part partial success.

- [ ] **Step 5: Commit**

```powershell
git add app/services/media/backends/base.py app/services/media/backends/router.py app/services/media/backends/ytdlp.py app/services/media/backends/cobalt.py app/services/media/downloader.py app/services/media/models.py tests/test_media_backend_router.py tests/test_cobalt_backend.py tests/test_media_downloader.py
git commit -m "feat: download multi-part media packages"
```

### Task 5: Deliver video, audio, photos, and cached albums through Telegram

**Files:**
- Modify: `app/services/telegram_files.py`
- Modify: `tests/test_telegram_files.py`

**Interfaces:**
- Produces: `TelegramPhotoInput(part_key: str, media: str, cached: bool)` where media is a local `file://` URI or Telegram `file_id`.
- Produces: `UploadResult(part_key: str, media_kind: MediaKind, message: Message, sent_as_document: bool)`.
- Produces: `TelegramFileService.send_artifacts(...) -> tuple[UploadResult, ...]` and media-kind-aware `send_cached(...)`.

- [ ] **Step 1: Write failing delivery tests**

Cover video fallback to document, M4A/MP3 through `send_audio`, Opus/OGG/unknown audio through `send_document`, a single photo through `send_photo`, cached type preservation, mixed cached/local photo inputs, and 11 ordered photos split into albums of 10 and 1 with the caption only on the first visual item.

- [ ] **Step 2: Run Telegram service tests and verify RED**

Run: `.\.venv\Scripts\python -m pytest tests/test_telegram_files.py -q`

Expected: failures because the service currently accepts exactly one video artifact.

- [ ] **Step 3: Implement media-kind-aware sending**

Route each artifact by media kind and actual suffix/MIME, build `InputMediaPhoto` groups without changing order, support local file URIs and cached IDs in the same group, and return one upload result per Telegram message file so every resulting `file_id` can be cached.

- [ ] **Step 4: Run Telegram service tests and verify GREEN**

Run: `.\.venv\Scripts\python -m pytest tests/test_telegram_files.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/services/telegram_files.py tests/test_telegram_files.py
git commit -m "feat: send Telegram media bundles"
```

### Task 6: Orchestrate manifest-aware cache reuse and partial delivery

**Files:**
- Modify: `app/services/download_manager.py`
- Modify: `app/database/repositories/media_cache.py`
- Modify: `tests/test_cleanup.py`
- Modify: `tests/test_media_repositories.py`
- Create: `tests/test_tiktok_bundle_delivery.py`

**Interfaces:**
- Consumes: prepared callback and part keys from Task 4, manifest repository from Task 3, and package-aware Telegram methods from Task 5.
- Produces: `DownloadManager.try_cached_publication(...) -> bool` for complete TikTok manifest hits.
- Produces: internal manifest resolution that returns missing part keys and ordered cached entries without redownloading valid parts.

- [ ] **Step 1: Write failing orchestration tests**

Test complete TikTok cache hits without backend calls; partial cache requesting only missing keys; video/photo content sent before audio; incomplete photo sets held until ordered albums can be assembled; one expired cached photo invalidating only `photo_NNN`; independent cache storage for every upload result; partial oversized/failing parts still completing with a notice; all-parts-failed state; and cleanup after partial success, timeout, and cancellation.

- [ ] **Step 2: Run manager tests and verify RED**

Run: `.\.venv\Scripts\python -m pytest tests/test_cleanup.py tests/test_media_repositories.py tests/test_tiktok_bundle_delivery.py -q`

Expected: failures for missing publication-cache orchestration and multi-result storage.

- [ ] **Step 3: Implement manifest-aware manager flow**

Check complete manifests before queueing; during a queued run persist prepared parts, resolve cache identities, ask the backend only for missing keys, merge cached/local photo inputs in manifest order, deliver visual parts then audio, invalidate only rejected cached IDs, store each new result using its own kind/key, and mark the request completed when at least one part succeeds. Make progress/error copy media-neutral and retain one progress message.

- [ ] **Step 4: Run manager tests and verify GREEN**

Run: `.\.venv\Scripts\python -m pytest tests/test_cleanup.py tests/test_media_repositories.py tests/test_tiktok_bundle_delivery.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/services/download_manager.py app/database/repositories/media_cache.py tests/test_cleanup.py tests/test_media_repositories.py tests/test_tiktok_bundle_delivery.py
git commit -m "feat: cache and deliver TikTok publication bundles"
```

### Task 7: Wire TikTok publication behavior and verify compatibility

**Files:**
- Modify: `app/services/media/adapters/tiktok.py`
- Modify: `app/bot/routers/downloader.py`
- Modify: `app/main.py`
- Modify: `tests/test_media_metadata.py`
- Modify: `tests/test_downloader_router.py`
- Modify: `tests/integration/test_external_services.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: all Tasks 1–6 interfaces.
- Produces: automatic TikTok option `tiktok_bundle` and user-facing video/photo/carousel-plus-audio behavior.

- [ ] **Step 1: Write failing router and adapter integration tests**

Assert TikTok canonical and short URLs create an automatic bundle request without yt-dlp metadata rejection; complete bundle cache is attempted before queue submission; cache miss queues one publication job; photo URLs no longer produce the old unsupported-photo text; and YouTube still presents its quality/audio keyboard.

- [ ] **Step 2: Run routing tests and verify RED**

Run: `.\.venv\Scripts\python -m pytest tests/test_media_metadata.py tests/test_downloader_router.py tests/integration/test_external_services.py -q`

Expected: unit failures for the old `best_video` TikTok contract; external-service tests remain skipped unless explicitly enabled.

- [ ] **Step 3: Implement final wiring and update operator documentation**

Rename the TikTok automatic option to `tiktok_bundle`, route it through publication cache handling, keep one queue slot per post, update messages from “video” to “publication/media” where necessary, and document YouTube audio plus TikTok video/photo/carousel behavior and the unchanged startup/migration commands.

- [ ] **Step 4: Run focused routing tests and verify GREEN**

Run: `.\.venv\Scripts\python -m pytest tests/test_media_metadata.py tests/test_downloader_router.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Run complete verification**

Run: `.\.venv\Scripts\python -m pytest -q`

Expected: all unit tests pass; only integration tests requiring `RUN_INTEGRATION_TESTS=1` are skipped.

Run: `.\.venv\Scripts\python -m ruff check .`

Expected: exit code 0.

Run: `.\.venv\Scripts\python -m ruff format --check .`

Expected: exit code 0.

Run: `.\.venv\Scripts\python -m alembic heads`

Expected: exactly one head, `20260930_0005`.

- [ ] **Step 6: Commit final wiring**

```powershell
git add app/services/media/adapters/tiktok.py app/bot/routers/downloader.py app/main.py tests/test_media_metadata.py tests/test_downloader_router.py tests/integration/test_external_services.py README.md
git commit -m "feat: support complete TikTok media posts"
```

- [ ] **Step 7: Request whole-branch review and address findings**

Review the implementation against `docs/superpowers/specs/2026-09-30-youtube-audio-tiktok-bundles-design.md`, especially the five Review Focus cases. Fix every Critical or Important finding with a failing regression test first, rerun the complete verification commands, and commit the fixes before declaring completion.
