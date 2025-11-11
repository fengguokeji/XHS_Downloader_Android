"""Core logic for exposing XiaoHongShu downloads through an API."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


XHS_LINK_PATTERN = re.compile(r"(?:https?://)?www\.xiaohongshu\.com/explore/\S+", re.IGNORECASE)
XHS_USER_PATTERN = re.compile(r"(?:https?://)?www\.xiaohongshu\.com/user/profile/[a-z0-9]+/\S+", re.IGNORECASE)
XHS_SHARE_PATTERN = re.compile(r"(?:https?://)?www\.xiaohongshu\.com/discovery/item/\S+", re.IGNORECASE)
XHS_SHORT_PATTERN = re.compile(r"(?:https?://)?xhslink\.com/[^\s\"<>\\^`{|}，。；！？、【】《》]+", re.IGNORECASE)

IMG_TAG_PATTERN = re.compile(r"<img[^>]+src\\s*=\\s*['\"]([^'\"]+)['\"][^>]*>", re.IGNORECASE)
MEDIA_URL_PATTERN = re.compile(
    r"https?://[\w\-._~:/?#\[\]@!$&'()*+,;=%]+\.(?:jpg|jpeg|png|gif|mp4|avi|mov|webm|wmv|f4v|swf|mpg|mpeg|asf|3gp|3g2|mkv|webp|heic|heif)",
    re.IGNORECASE,
)
INVALID_JSON_VALUE_PATTERN = re.compile(r":\s*(?:undefined|NaN|[+-]?Infinity)")
UNICODE_ESCAPE_PATTERN = re.compile(r"\\u([0-9a-fA-F]{4})")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=1.0,image/avif,image/webp,image/apng,*/*;q=0.8",
}

SHORT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/117.0.0.0 Mobile Safari/537.36 xiaohongshu"
    )
}


@dataclass
class MediaItem:
    """Represents a single piece of media referenced by a note."""

    type: str
    url: Optional[str] = None
    image_url: Optional[str] = None
    video_url: Optional[str] = None


@dataclass
class NoteResult:
    """Structured response for a single XiaoHongShu note."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    media: List[MediaItem] = field(default_factory=list)
    raw_media_urls: List[str] = field(default_factory=list)


@dataclass
class UrlResult:
    """Information gathered for a single requested URL."""

    requested_url: str
    resolved_url: str
    notes: List[NoteResult] = field(default_factory=list)
    fallback_media_urls: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class DownloadResult:
    """Top level API response payload."""

    input: str
    results: List[UrlResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class XHSDownloaderAPI:
    """Expose the downloader logic in a platform agnostic way."""

    def __init__(self, *, timeout: float = 15.0) -> None:
        self._client = httpx.Client(follow_redirects=True, timeout=timeout)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def process(self, input_text: str) -> DownloadResult:
        """Process an arbitrary string and extract structured media information."""

        cleaned_input = (input_text or "").strip()
        if not cleaned_input:
            raise ValueError("Input cannot be empty.")

        url_candidates = self._extract_links(cleaned_input)
        if not url_candidates:
            raise ValueError("No valid XiaoHongShu URLs were found in the provided text.")

        results: List[UrlResult] = []
        for original, resolved in url_candidates:
            logger.debug("Processing URL - original=%s resolved=%s", original, resolved)
            url_result = UrlResult(requested_url=original, resolved_url=resolved)

            html = self._fetch_post_details(resolved)
            if not html:
                url_result.error = "Failed to fetch post details."
                results.append(url_result)
                continue

            notes, fallback_urls = self._parse_post_details(html)
            url_result.notes = notes
            url_result.fallback_media_urls = fallback_urls

            if not notes and not fallback_urls:
                url_result.error = "No media could be extracted from the provided URL."

            results.append(url_result)

        return DownloadResult(input=cleaned_input, results=results)

    # ------------------------------------------------------------------
    # Link extraction helpers
    # ------------------------------------------------------------------
    def _extract_links(self, input_text: str) -> List[Tuple[str, str]]:
        """Extract and normalise XiaoHongShu links from an arbitrary string."""

        urls: List[Tuple[str, str]] = []
        for part in re.split(r"\s+", input_text):
            if not part:
                continue

            processed_part = part

            short_match = XHS_SHORT_PATTERN.search(part)
            if short_match:
                short_url = part[short_match.start() : short_match.end()]
                resolved = self._resolve_short_url(short_url) or short_url
                urls.append((short_url, resolved))
                continue

            share_match = XHS_SHARE_PATTERN.search(processed_part)
            if share_match:
                matched = processed_part[share_match.start() : share_match.end()]
                urls.append((matched, matched))
                continue

            link_match = XHS_LINK_PATTERN.search(processed_part)
            if link_match:
                matched = processed_part[link_match.start() : link_match.end()]
                urls.append((matched, matched))
                continue

            user_match = XHS_USER_PATTERN.search(processed_part)
            if user_match:
                matched = processed_part[user_match.start() : user_match.end()]
                urls.append((matched, matched))

        return urls

    def _resolve_short_url(self, short_url: str) -> Optional[str]:
        try:
            response = self._client.get(short_url, headers=SHORT_HEADERS)
            response.raise_for_status()
            return str(response.request.url)
        except Exception as exc:  # pragma: no cover - network failures are non-deterministic
            logger.warning("Failed to resolve short url %s: %s", short_url, exc)
            return None

    # ------------------------------------------------------------------
    # Network helpers
    # ------------------------------------------------------------------
    def _fetch_post_details(self, url: str) -> Optional[str]:
        try:
            response = self._client.get(url, headers=DEFAULT_HEADERS)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # pragma: no cover - depends on external service
            logger.warning("Failed to fetch post details from %s: %s", url, exc)
            return None

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------
    def _parse_post_details(self, html: str) -> Tuple[List[NoteResult], List[str]]:
        notes: List[NoteResult] = []
        fallback_urls: List[str] = []

        script_index = html.find("window.__INITIAL_STATE__=")
        if script_index != -1:
            end_index = html.find("</script>", script_index)
            if end_index != -1:
                script_content = html[script_index:end_index]
                equals_index = script_content.find("=")
                if equals_index != -1:
                    json_payload = script_content[equals_index + 1 :].strip()
                    if json_payload.endswith(";"):
                        json_payload = json_payload[:-1]

                    try:
                        normalised_payload = self._normalise_json_payload(json_payload)
                        root = json.loads(normalised_payload)
                        note_objects = list(self._extract_note_objects(root))
                        for note in note_objects:
                            media_items, raw_urls = self._extract_media_from_note(note)
                            metadata = self._extract_note_metadata(note)
                            if media_items or raw_urls:
                                notes.append(
                                    NoteResult(metadata=metadata, media=media_items, raw_media_urls=raw_urls)
                                )
                    except json.JSONDecodeError as exc:
                        logger.warning("Failed to parse JSON payload: %s", exc)
                        fallback_urls.extend(self._extract_urls_from_html(html))
                    except Exception as exc:  # pragma: no cover - defensive coding
                        logger.warning("Unexpected error while processing JSON payload: %s", exc)
                        fallback_urls.extend(self._extract_urls_from_html(html))
        else:
            fallback_urls.extend(self._extract_urls_from_html(html))

        # Ensure fallback URLs are unique and preserve order
        if fallback_urls:
            seen: set[str] = set()
            unique_fallback = []
            for url in fallback_urls:
                if url not in seen:
                    seen.add(url)
                    unique_fallback.append(url)
            fallback_urls = unique_fallback

        return notes, fallback_urls

    def _extract_note_objects(self, root: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        if not isinstance(root, dict):
            return []

        note_root = root.get("note")
        if isinstance(note_root, dict):
            note_detail_map = note_root.get("noteDetailMap")
            if isinstance(note_detail_map, dict):
                for value in note_detail_map.values():
                    note_data = value.get("note") if isinstance(value, dict) else None
                    if isinstance(note_data, dict):
                        yield note_data
                return

            note_candidate = note_root.get("note")
            if isinstance(note_candidate, dict):
                yield note_candidate
                return

            feed = note_root.get("feed")
            if isinstance(feed, dict):
                items = feed.get("items")
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            yield item
                return

            if any(key in note_root for key in ("imageList", "video")):
                yield note_root
                return

        feed_root = root.get("feed")
        if isinstance(feed_root, dict):
            items = feed_root.get("items")
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        yield item
                return

        for value in root.values():
            if isinstance(value, dict) and any(
                key in value for key in ("note", "imageList", "images", "video")
            ):
                yield value

    def _extract_media_from_note(self, note: Dict[str, Any]) -> Tuple[List[MediaItem], List[str]]:
        media_items: List[MediaItem] = []
        raw_urls: List[str] = []

        video = note.get("video")
        if isinstance(video, dict):
            consumer = video.get("consumer")
            if isinstance(consumer, dict) and isinstance(consumer.get("originVideoKey"), str):
                video_url = f"https://sns-video-bd.xhscdn.com/{consumer['originVideoKey']}"
                media_items.append(MediaItem(type="video", url=video_url))
                raw_urls.append(video_url)
            else:
                media_urls = self._extract_video_streams(video)
                for url in media_urls:
                    media_items.append(MediaItem(type="video", url=url))
                    raw_urls.append(url)

        image_list: Optional[List[Dict[str, Any]]] = None
        if isinstance(note.get("imageList"), list):
            image_list = note.get("imageList")
        elif isinstance(note.get("images"), list):
            image_list = note.get("images")
        elif isinstance(note.get("image"), dict):
            image_list = [note.get("image")]  # type: ignore[list-item]

        if image_list:
            for image in image_list:
                if not isinstance(image, dict):
                    continue
                image_url = self._extract_image_url(image)
                live_photo_video_url = self._extract_live_photo_video(image)

                if image_url:
                    transformed_image = self._transform_xhs_cdn_url(image_url)
                    if live_photo_video_url:
                        transformed_video = self._transform_xhs_cdn_url(live_photo_video_url)
                        media_items.append(
                            MediaItem(
                                type="live_photo",
                                image_url=transformed_image,
                                video_url=transformed_video,
                            )
                        )
                        raw_urls.extend(filter(None, [transformed_image, transformed_video]))
                    else:
                        media_items.append(MediaItem(type="image", url=transformed_image))
                        raw_urls.append(transformed_image)

        if not media_items:
            for value in note.values():
                if isinstance(value, str) and self._is_valid_media_url(value):
                    media_items.append(MediaItem(type="raw", url=value))
                    raw_urls.append(value)

        return media_items, raw_urls

    def _extract_video_streams(self, video_obj: Dict[str, Any]) -> List[str]:
        urls: List[str] = []
        media = video_obj.get("media")
        if isinstance(media, dict):
            stream = media.get("stream")
            if isinstance(stream, dict):
                h264 = stream.get("h264")
                if isinstance(h264, list):
                    for entry in h264:
                        if isinstance(entry, str) and entry.startswith("http"):
                            urls.append(entry)
                        elif isinstance(entry, dict):
                            for key in ("url", "masterUrl"):
                                value = entry.get(key)
                                if isinstance(value, str):
                                    urls.append(value)
        return urls

    def _extract_image_url(self, image_obj: Dict[str, Any]) -> Optional[str]:
        for key in ("urlDefault", "url", "traceId"):
            value = image_obj.get(key)
            if isinstance(value, str):
                if key == "traceId":
                    return f"https://sns-img-qc.xhscdn.com/{value}"
                return value
        info_list = image_obj.get("infoList")
        if isinstance(info_list, list):
            for info in info_list:
                if isinstance(info, dict) and isinstance(info.get("url"), str):
                    return info["url"]
        return None

    def _extract_live_photo_video(self, image_obj: Dict[str, Any]) -> Optional[str]:
        stream = image_obj.get("stream")
        if isinstance(stream, dict):
            h264 = stream.get("h264")
            if isinstance(h264, list) and h264:
                first = h264[0]
                if isinstance(first, dict):
                    for key in ("masterUrl", "url"):
                        value = first.get(key)
                        if isinstance(value, str):
                            return value
        return None

    def _extract_note_metadata(self, note: Dict[str, Any]) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}
        desc = self._extract_note_description(note)
        if desc:
            metadata["desc"] = desc

        title = note.get("title")
        if isinstance(title, str) and title and title != desc:
            metadata["title"] = title

        note_id = note.get("noteId")
        if isinstance(note_id, str):
            metadata["noteId"] = note_id

        user = note.get("user")
        if isinstance(user, dict):
            nickname = user.get("nickname")
            if isinstance(nickname, str):
                metadata["author"] = nickname

        interact_info = note.get("interactInfo")
        if isinstance(interact_info, dict):
            for key, target in ("likedCount", "likes"), ("commentCount", "comments"):
                value = interact_info.get(key)
                if isinstance(value, (str, int)):
                    metadata[target] = value

        tag_list = note.get("tagList")
        if isinstance(tag_list, list):
            tags: List[str] = []
            for tag in tag_list:
                if isinstance(tag, dict) and isinstance(tag.get("name"), str):
                    tags.append(tag["name"])
            if tags:
                metadata["tags"] = tags

        return metadata

    def _extract_note_description(self, note: Dict[str, Any]) -> Optional[str]:
        for key in ("desc", "description", "title"):
            value = note.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _extract_urls_from_html(self, html: str) -> List[str]:
        normalised_html = self._decode_unicode_sequences(html)

        urls: List[str] = []
        for match in IMG_TAG_PATTERN.finditer(normalised_html):
            url = match.group(1)
            if self._is_valid_media_url(url):
                urls.append(url)
        for match in MEDIA_URL_PATTERN.finditer(normalised_html):
            url = match.group(0)
            if self._is_valid_media_url(url) and url not in urls:
                urls.append(url)
        return urls

    def _normalise_json_payload(self, payload: str) -> str:
        """Replace JavaScript-only tokens so the payload becomes valid JSON."""

        # Fast path for the common case where a simple substitution is enough.
        if "undefined" not in payload and "Infinity" not in payload and "NaN" not in payload:
            return payload

        # First handle straightforward ``: undefined`` patterns using a regex to minimise
        # the amount of work the slower normaliser below has to do. This keeps behaviour
        # identical for existing payloads while still allowing us to catch occurrences in
        # arrays or other edge-cases.
        payload = INVALID_JSON_VALUE_PATTERN.sub(": null", payload)

        tokens: List[Tuple[str, str]] = [
            ("+Infinity", "null"),
            ("-Infinity", "null"),
            ("Infinity", "null"),
            ("undefined", "null"),
            ("NaN", "null"),
        ]

        def is_identifier(char: str) -> bool:
            return char.isalnum() or char in {"_", "$"}

        result: List[str] = []
        in_string = False
        escape = False
        quote_char = ""
        i = 0
        length = len(payload)

        while i < length:
            ch = payload[i]

            if in_string:
                result.append(ch)
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote_char:
                    in_string = False
                i += 1
                continue

            if ch in {'"', "'"}:
                in_string = True
                quote_char = ch
                result.append(ch)
                i += 1
                continue

            replaced = False
            for token, replacement in tokens:
                if payload.startswith(token, i):
                    start_index = i
                    end_index = i + len(token)
                    prev_char = payload[start_index - 1] if start_index > 0 else ""
                    next_char = payload[end_index] if end_index < length else ""

                    if not is_identifier(prev_char) and not is_identifier(next_char):
                        result.append(replacement)
                        i += len(token)
                        replaced = True
                        break

            if replaced:
                continue

            result.append(ch)
            i += 1

        return "".join(result)

    def _decode_unicode_sequences(self, text: str) -> str:
        """Decode escaped characters often embedded in XiaoHongShu HTML payloads."""

        text = text.replace("\\/", "/")
        return UNICODE_ESCAPE_PATTERN.sub(lambda match: chr(int(match.group(1), 16)), text)

    def _is_valid_media_url(self, url: Optional[str]) -> bool:
        if not isinstance(url, str):
            return False
        lower = url.lower()
        return any(
            ext in lower
            for ext in (
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".mp4",
                ".webm",
                "xhscdn.com",
                "xiaohongshu.com",
            )
        )

    def _transform_xhs_cdn_url(self, url: Optional[str]) -> Optional[str]:
        if not isinstance(url, str):
            return None
        if "xhscdn.com" not in url:
            return url
        lower = url.lower()
        if any(token in lower for token in ("video", "sns-video")):
            return url
        parts = url.split("/")
        if len(parts) <= 5:
            return url
        token = "/".join(parts[5:])
        token = re.split(r"[!?]", token)[0]
        return f"https://ci.xiaohongshu.com/{token}"

    # Compatibility aliases -------------------------------------------------
    transform_xhs_cdn_url = _transform_xhs_cdn_url


__all__ = [
    "DownloadResult",
    "MediaItem",
    "NoteResult",
    "UrlResult",
    "XHSDownloaderAPI",
]
