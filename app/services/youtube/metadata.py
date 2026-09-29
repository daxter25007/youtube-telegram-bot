"""Совместимые импорты для прежнего пути YouTube-сервиса."""

from app.services.media.errors import MetadataError
from app.services.media.metadata import YtDlpMetadataService
from app.services.media.models import MediaMetadata

MetadataService = YtDlpMetadataService
VideoMetadata = MediaMetadata

__all__ = ["MetadataError", "MetadataService", "VideoMetadata"]
