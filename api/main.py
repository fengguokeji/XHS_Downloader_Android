"""FastAPI entrypoint exposing the XiaoHongShu downloader as an HTTP API."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .xhs_downloader_api import XHSDownloaderAPI

app = FastAPI(
    title="XiaoHongShu Downloader API",
    description="Expose the downloader logic used by the Android client as a reusable HTTP service.",
    version="1.0.0",
)

downloader = XHSDownloaderAPI()


class ExtractRequest(BaseModel):
    """Payload for requesting media extraction."""

    url: str = Field(..., description="A XiaoHongShu note URL or any text containing one.")


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Simple health-check endpoint."""

    return {"status": "ok"}


async def _execute_extract(url: str) -> dict:
    """Shared logic for invoking the downloader and normalising errors."""

    try:
        result = await run_in_threadpool(downloader.process, url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive coding for unexpected failures
        raise HTTPException(status_code=500, detail=f"Failed to process request: {exc}") from exc

    return result.to_dict()


@app.post("/api/extract", tags=["downloader"])
async def extract_media(payload: ExtractRequest) -> dict:
    """Resolve the given XiaoHongShu URL and return the parsed media information."""

    return await _execute_extract(payload.url)


@app.get("/api/extract", tags=["downloader"])
async def extract_media_get(url: str) -> dict:
    """GET-compatible variant of :func:`extract_media` using a query parameter."""

    return await _execute_extract(url)


__all__ = ["app"]
