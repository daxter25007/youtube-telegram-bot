# Multi-platform Media Adapters Design

## Context and intent

The bot currently downloads YouTube videos through a pipeline built around yt-dlp,
ffmpeg, a shared temporary volume, PostgreSQL, and Telegram Local Bot API. The next
supported source is TikTok video. Instagram, VK, RuTube, TikTok photo carousels, and
audio-only downloads are planned, so the implementation must remove YouTube-specific
coupling instead of adding TikTok branches throughout the existing code.

The immediate user-visible scope is:

- keep current YouTube video behavior, including quality selection;
- accept public TikTok video URLs, including official short links;
- download the best available TikTok video automatically;
- reject unsupported TikTok photo posts with a clear message;
- preserve queueing, limits, subscriptions, statistics, timeouts, cleanup, and Local
  Bot API uploads.

## Chosen approach

Introduce a source-adapter layer over shared yt-dlp infrastructure. Source adapters
own URL rules and platform policy; shared services own subprocess execution, storage,
queueing, file validation, and Telegram delivery.

This is preferred over adding TikTok checks to the current YouTube services because
the latter would make each future source require changes across routing, metadata,
download, and persistence code. Building all future photo and audio behavior now is
also rejected: the contracts will accommodate those media kinds, but only video will
be implemented in this change.

## Domain model

The media subsystem exposes platform-neutral immutable values:

- `MediaSource`: `youtube` and `tiktok` initially;
- `MediaKind`: `video`, with reserved domain values for future `photo` and `audio`;
- `ParsedMediaUrl`: source, normalized URL, and optional source media ID;
- `MediaMetadata`: source ID, normalized URL, title, duration, thumbnail, media kind,
  and available download options;
- `DownloadOption`: stable short key, display label, media kind, optional height,
  estimated size, and adapter-owned selector data;
- `MediaArtifact`: local path, media kind, dimensions, duration, and optional MIME
  information;
- `MediaPackage`: one or more artifacts produced by one download job.

The current implementation creates one video artifact per package. A future carousel
can create multiple photo artifacts without changing the queue or job lifecycle.

`DownloadOption` is stored in the server-side request metadata. Telegram callbacks
contain only a short request ID and option key; they never contain a URL or raw yt-dlp
arguments.

## Component boundaries

### Media registry

`MediaRegistry` receives user text, performs common URL safety validation, and selects
exactly one adapter. It is the only entry point used by the Telegram router for source
detection.

### Source adapters

Each adapter implements a common interface for:

- matching and normalizing allowed URLs;
- converting raw yt-dlp metadata into `MediaMetadata`;
- producing supported `DownloadOption` values;
- resolving an option key into trusted yt-dlp format policy;
- classifying platform-specific extraction failures;
- declaring whether selection is interactive or automatic.

`YouTubeAdapter` preserves the existing 360p through 2160p quality behavior.
`TikTokAdapter` accepts video posts and creates one `best_video` option. Photo-only
posts are recognized from metadata and rejected as unsupported for this release.

Adapters do not spawn processes, create temporary directories, write to PostgreSQL,
or call Telegram.

### Shared yt-dlp services

A platform-neutral metadata extractor runs yt-dlp with `--dump-single-json`,
`--skip-download`, `--no-playlist`, timeouts, and optional runtime cookies. It passes
the raw result to the selected adapter.

A platform-neutral downloader receives a trusted adapter-produced download policy,
uses `asyncio.create_subprocess_exec`, emits throttled progress, and returns a
`MediaPackage`. ffmpeg remuxing and ffprobe inspection remain shared. No command uses
`shell=True`.

### Queue and download manager

The queue remains source-independent. The download manager receives source URL,
source, option key, title, and media kind. It rechecks subscription status, owns the
job directory, applies the configured timeout and file-size limit, updates database
status, sends the resulting package, and deletes the directory in `finally`.

The manager is responsible for the lifecycle of all artifacts in a package. It must
never leave a partial TikTok or YouTube file after success, failure, cancellation,
timeout, or Telegram upload error.

### Telegram delivery

The sender accepts a `MediaPackage`. In this release it sends its single video using
`sendVideo` with a Local Bot API `file://` path, then falls back to `sendDocument`.
The interface permits future media groups and audio delivery without changing the
download manager contract.

## URL policy

Common validation requires:

- HTTPS scheme;
- no username or password component;
- no explicit port;
- a hostname from an adapter's exact allowlist;
- no IP literal, localhost, private address, file URL, FTP URL, or scheme-relative
  input;
- no whitespace or control characters that alter URL interpretation.

Initial TikTok host allowlist:

- `tiktok.com`;
- `www.tiktok.com`;
- `m.tiktok.com`;
- `vm.tiktok.com`;
- `vt.tiktok.com`.

Canonical TikTok URLs must represent `/@<account>/video/<numeric-id>`. Official short
links may use an opaque path because TikTok resolves them; the initial host is still
strictly allowlisted. yt-dlp receives the URL as one argument after `--`, so user text
cannot become a command-line option.

Playlist/profile URLs are rejected. Redirect results do not grant trust to arbitrary
user input: redirect handling remains inside the known TikTok extractor rather than a
generic application HTTP fetcher.

## User flows

### YouTube

The existing flow remains:

1. validate and normalize the URL;
2. fetch metadata;
3. show only available, size-compatible resolutions;
4. verify request ownership and TTL on selection;
5. queue, download, upload, record statistics, and clean up.

### TikTok video

The TikTok flow is:

1. validate the canonical or official short URL;
2. fetch metadata through the TikTok adapter;
3. ensure the post contains downloadable video;
4. reject an estimate already above the upload limit;
5. show the title and duration, then enqueue the trusted `best_video` option without
   asking for a redundant quality choice;
6. download, inspect, apply the actual size limit, upload, record statistics, and
   clean up.

All existing rate limits and per-user/global concurrency limits apply equally to both
sources.

## Persistence migration

An Alembic migration generalizes existing tables without discarding history.

For both `downloads` and `download_requests`:

- rename `youtube_url` to `source_url`;
- rename `youtube_video_id` to `source_media_id`;
- add non-null `source`, backfilled to `youtube` for existing rows;
- add non-null `media_kind`, backfilled to `video` for existing rows.

For `downloads`:

- add `requested_option` as a non-null string;
- backfill it from existing `requested_quality` as `video_<height>`;
- retain `requested_quality` temporarily as nullable compatibility/statistics data;
- keep `actual_quality` nullable for video height.

New code writes `requested_option`; YouTube also writes numeric
`requested_quality`. TikTok writes `best_video` and leaves `requested_quality` null.
This avoids destroying existing quality statistics while allowing future non-video
options. Removing the compatibility column is outside this change.

The request JSON remains the canonical server-side snapshot of adapter-produced
options. Existing unexpired YouTube requests created before deployment may be cleaned
up at startup instead of being migrated between metadata schema versions.

## Error handling

Internal failures retain structured categories and sanitized logs. Users receive no
traceback or raw yt-dlp output.

TikTok user-facing categories include:

- deleted, private, or unavailable post;
- authentication or cookies required;
- regional restriction;
- unsupported photo-only post;
- temporary TikTok/yt-dlp extraction incompatibility;
- estimated or actual file too large;
- download timeout;
- Telegram upload failure.

The existing optional cookie file is shared by yt-dlp adapters and mounted only at
runtime. The default setup requires no cookies. Documentation will explain that
TikTok protection can change and that a current yt-dlp build or runtime cookies may
occasionally be necessary.

## Security properties

- URLs are accepted only by exact source adapters.
- Users cannot supply subprocess flags, format selectors, paths, or filenames.
- Output paths are resolved and must remain direct children of the generated job
  directory.
- Display titles are sanitized and never used to construct storage paths.
- Callback ownership and TTL checks remain mandatory.
- File size is checked before Telegram upload using the configured safety margin.
- All subprocesses are terminated on timeout, cancellation, and shutdown.
- All job directories are deleted in `finally`; startup and periodic stale cleanup
  remain enabled.
- Tokens, cookies, API hashes, database credentials, and token-bearing URLs are
  excluded from logs and the repository.

## Testing strategy

Unit tests use fixture metadata and fake subprocesses; they never download real
social-media content.

Coverage includes:

- canonical and official short TikTok URLs;
- deceptive sibling domains, HTTP, credentials, ports, IPs, local hosts, invalid
  paths, profile URLs, and control characters;
- media registry dispatch for YouTube and TikTok;
- TikTok video metadata and `best_video` option;
- rejection of photo-only TikTok metadata;
- source-specific error classification;
- safe yt-dlp argument construction;
- automatic TikTok queueing and retained YouTube quality callbacks;
- request ownership and expiry for both sources;
- timeout, cancellation, oversized output, upload failure, and cleanup;
- repository reads and writes with generalized fields;
- migration backfill for existing YouTube rows;
- unchanged YouTube format selection and security behavior.

An optional integration test can run only when
`RUN_INTEGRATION_TESTS=1` and a separate TikTok URL environment variable are set. It
is excluded from normal `pytest` runs.

Verification for this significant feature includes the full unit suite, Ruff checks,
format check, Alembic migration validation, Compose configuration validation, and a
repository secret scan where the required scanner is available. Docker containers
will not be started or restarted unless the user explicitly requests it.

## Documentation and operations

README and the `/start` text will advertise YouTube and TikTok video support only.
The README will document accepted TikTok links, unsupported photo posts, optional
cookies, platform volatility, upgrade procedure for yt-dlp, database migration, and
the unchanged Local Bot API shared-volume flow.

No new externally published ports or services are required. Existing resource,
timeout, concurrency, rate-limit, and upload-size settings apply to TikTok.

## Acceptance criteria

- Existing YouTube links behave as before.
- A valid public TikTok video or official short link is automatically queued and sent
  through Local Bot API.
- TikTok photo-only posts fail with a clear unsupported-format message.
- Invalid or malicious TikTok-like URLs never reach yt-dlp.
- Existing YouTube database history survives migration.
- New records identify source, media kind, source media ID, URL, and requested option.
- TikTok uses the same queue, subscription checks, limits, timeouts, cleanup, and
  statistics lifecycle as YouTube.
- The architecture admits a new adapter without modifying shared subprocess, queue,
  storage, or Telegram lifecycle code.
