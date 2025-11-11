"""Utilities for exposing the XiaoHongShu downloader as an HTTP API."""

from .xhs import XHSDownloaderAPI, DownloadResult

__all__ = ["XHSDownloaderAPI", "DownloadResult"]
