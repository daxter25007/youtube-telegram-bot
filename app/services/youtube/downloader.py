"""Совместимые импорты для прежнего пути YouTube-сервиса."""

from app.services.media.downloader import DownloadError, MediaDownloader
from app.services.media.models import MediaArtifact

YouTubeDownloader = MediaDownloader
DownloadedMedia = MediaArtifact

__all__ = ["DownloadError", "DownloadedMedia", "YouTubeDownloader"]
