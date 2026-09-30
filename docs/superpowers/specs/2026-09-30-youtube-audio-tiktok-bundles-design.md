# YouTube Audio and TikTok Media Bundles

## Goal

Extend the bot from one-video-per-request delivery to publication-aware delivery:

- YouTube keeps its quality picker and adds a `🎵 Только аудио` option that produces M4A.
- Every TikTok request is automatic and delivers all visual content plus its attached sound:
  - a video post produces a video message and a separate audio message;
  - a photo post produces one photo message and a separate audio message;
  - a carousel produces ordered Telegram albums of at most ten photos, followed by a separate audio message.
- Every delivered file is cached by Telegram `file_id`. A repeated complete request is served without downloading the media again.
- A failure of one part must not suppress parts that can still be delivered.

The attached TikTok sound means the original sound associated with the post. Cobalt will be asked for audio-only output with `audioFormat: "best"` and TikTok full-audio support enabled. The returned container is preserved.

## User Experience

### YouTube

The existing two-column quality grid remains. A final single-button row contains `🎵 Только аудио`. Cached video qualities and cached audio retain the existing instant-download marker.

Selecting audio uses the same queue, progress message, limits, cache, and cancellation behavior as selecting a video quality. The result is sent through Telegram's audio player as M4A.

### TikTok

TikTok remains an automatic flow with no choice screen. One compact progress message represents the whole publication. Successful parts are sent as separate Telegram messages:

1. visual content first;
2. attached audio second.

A single photo is sent with `sendPhoto`. Carousels are sent with `sendMediaGroup` in source order and split into groups of at most ten items. MP3 and M4A sound is sent with `sendAudio`; any other source format is sent with `sendDocument` so the bot does not transcode or rename the media misleadingly.

If only some parts fail, the progress message reports the partial result after all viable parts have been attempted. It does not delete or retract successful messages.

## Architecture

### Publication manifest

Introduce a persistent manifest for multi-part media. A manifest belongs to `(source, source_media_id)` and stores the ordered expected part descriptors:

- stable part key such as `video`, `audio`, or `photo_000`;
- `MediaKind`;
- original position;
- optional filename and MIME/container hints.

The manifest lets the bot distinguish a complete cached publication from a previous partial delivery. It is updated transactionally after Cobalt describes a post and before individual media streams are downloaded.

No manifest is needed for the existing single-option YouTube flow. YouTube continues using the selected option key (`video_720`, `audio_m4a`) as its cache key.

### Cobalt client

Replace the single-URL Cobalt response model with a normalized ordered collection of prepared items. The client will:

- accept an explicit mode (`auto` or `audio`);
- request TikTok visuals with `downloadMode: "auto"`;
- request the attached sound with `downloadMode: "audio"`, `audioFormat: "best"`, and full TikTok audio enabled;
- retain every photo/video entry from `picker` responses rather than selecting only the first video;
- consume an audio URL embedded in a picker response when present, avoiding a duplicate request;
- validate every returned URL through the existing SSRF protection before exposing it to the backend.

Unexpected picker entries are ignored only when at least one supported item remains. A response with no supported media is classified as unsupported.

### Download model and backends

`MediaArtifact` gains a stable part/cache key. `MediaPackage` remains the unit returned by a backend but may now contain multiple artifacts of different kinds.

The Cobalt backend downloads each normalized item into a unique path while retaining its detected content type and source extension. Size limits are enforced per Telegram-bound file, matching Telegram's per-file upload limit. An individual item failure is collected and does not stop remaining items unless the failure is a publication-wide security error.

The yt-dlp backend gains an audio policy:

- YouTube uses audio extraction and conversion/remuxing to M4A.
- TikTok remains a fallback for video/audio posts when Cobalt has a retryable failure.
- TikTok photo and carousel support requires Cobalt; yt-dlp fallback does not fabricate partial photo metadata.

Backend routing retains the current retry policy. A successful partial TikTok package is deliverable and records failed part keys for the final user-facing status.

### Telegram delivery

The Telegram file service becomes package-aware:

- video: `sendVideo`, with the existing document fallback when Telegram cannot process it as video;
- photo: `sendPhoto` or ordered `sendMediaGroup` batches;
- audio: `sendAudio` for MP3/M4A, otherwise `sendDocument`;
- cached delivery mirrors the original send method because Telegram cannot change a cached `file_id` type.

Upload results are returned per artifact and contain the part key, Telegram `file_id`, media kind, and whether the item was sent as a document. Each successful result is cached independently.

## Cache Flow

For YouTube, lookup stays scoped to source, source media ID, media kind, and selected option key.

For TikTok:

1. Look up the publication manifest and all cached entries.
2. If every manifest part is cached, send the complete package immediately without contacting Cobalt.
3. If the manifest is absent, enqueue discovery and download.
4. If the manifest exists but is partial, reuse valid cached entries and download only missing parts.
5. Preserve carousel order: cached photos are sent immediately only when the complete visual set is available. Otherwise the bot waits for missing photos, then assembles ordered albums. Independently complete video or audio parts may be sent immediately.
6. If Telegram rejects a cached `file_id`, invalidate only that cache entry and redownload that part.

The short TikTok URL hash remains the stable media ID for repeated identical short links. Canonical links continue using the TikTok numeric ID.

## Data and Compatibility

Add an Alembic migration for a `media_manifests` table with a unique `(source, source_media_id)` constraint and JSON part descriptors. Existing download and cache rows remain valid; no destructive migration is required.

The existing `media_cache` uniqueness model is reused by assigning every artifact a distinct option/part key. Existing video cache entries continue to resolve unchanged.

Download records continue to represent one user request. For TikTok they represent the publication job, while per-part outcomes live in the package result and cache. Router enqueueing must use the selected option's media kind for YouTube so the new audio option is recorded and cached as audio.

## Error Handling

- Unsafe URLs fail the affected Cobalt response and are never fetched.
- Oversized files are skipped individually and reported in the final progress text.
- Unsupported Telegram audio containers are sent as documents rather than transcoded.
- If every part fails, the existing failed-download state and error message are used.
- If at least one part succeeds, the download is completed with a partial-delivery notice listing the unavailable media categories.
- Temporary files for every successful or failed part are removed by the existing job-directory cleanup boundary.

## Tests

Implementation follows test-first development. Coverage will include:

- YouTube metadata exposes `audio_m4a`, the keyboard places it on its own row, and the adapter creates an M4A audio policy.
- The router records and caches the selected option's `MediaKind.AUDIO`.
- Cobalt request bodies for visual and audio modes.
- Picker parsing for one photo, ordered carousel photos, video, embedded audio, and malformed/unsafe entries.
- Cobalt multi-artifact downloads, per-file size checks, unique paths, partial failures, and cleanup.
- Telegram upload and cached resend behavior for video, photo albums, M4A/MP3 audio, and other audio containers.
- Manifest completeness, full cache hits, partial cache hits, missing-part retries, invalid `file_id` recovery, and carousel ordering.
- TikTok end-to-end orchestration for video plus audio, single photo plus audio, and carousel plus audio.
- Existing YouTube video qualities, TikTok video fallback, rate limits, queue limits, and old cache entries remain covered by the full suite.

## Out of Scope

- Audio bitrate selection.
- MP3 conversion for YouTube.
- User-selectable TikTok download modes.
- Combining TikTok photos into a generated video.
- Changing Telegram's configured per-file upload limit.
