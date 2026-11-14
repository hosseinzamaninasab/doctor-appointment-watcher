"""Shared infrastructure for appointment providers.

Holds the provider contract plus the HTTP and Next.js parsing helpers that
every provider adapter reuses. Provider-specific URL validation, doctor ID
extraction and appointment parsing live in the individual provider modules.
"""

from __future__ import annotations

import gzip
import json
import re
import time
from typing import Any, Callable, NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen


REQUEST_TIMEOUT_SECONDS = 45
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; Mobile) "
    "AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36"
)


class WatcherError(RuntimeError):
    """A recoverable watcher error."""


class WatcherHTTPError(WatcherError):
    """A watcher error carrying the HTTP status and response body, if any.

    Callers such as the Telegram client use the status to react to specific
    conditions (for example HTTP 429 and its retry_after hint).
    """

    def __init__(self, message: str, *, status: int | None = None, body: bytes | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


def encode_url(url: str) -> str:
    """Percent-encode a URL so non-ASCII paths (e.g. Persian slugs) are
    safe for urllib. ASCII components pass through unchanged."""
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(parts.path),
            parts.query,
            parts.fragment,
        )
    )


def request_bytes(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    attempts: int = 3,
) -> bytes:
    request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        request_headers.update(headers)

    last_error: Exception | None = None
    last_status: int | None = None
    last_body: bytes | None = None

    for attempt in range(1, attempts + 1):
        try:
            request = Request(encode_url(url), data=data, headers=request_headers)
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read(MAX_DOWNLOAD_BYTES + 1)

            if len(body) > MAX_DOWNLOAD_BYTES:
                raise WatcherError("Website response is too large.")

            return body

        except (HTTPError, URLError, TimeoutError, OSError, WatcherError) as exc:
            last_error = exc

            if isinstance(exc, HTTPError):
                last_status = exc.code
                try:
                    last_body = exc.read(MAX_DOWNLOAD_BYTES + 1)
                except Exception:
                    last_body = None

            if attempt < attempts:
                time.sleep(2**attempt)

    if isinstance(last_error, HTTPError):
        raise WatcherHTTPError(
            f"Connection failed: {last_error}", status=last_status, body=last_body
        )

    raise WatcherError(f"Connection failed: {last_error}")


def request_json(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    raw = request_bytes(url, data=data, headers=headers)

    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WatcherError("Received an invalid JSON response.") from exc

    if not isinstance(result, dict):
        raise WatcherError("Received an unexpected JSON structure.")

    return result


def fetch_html(url: str) -> str:
    raw = request_bytes(
        url,
        headers={"Accept": "text/html,application/xhtml+xml"},
    )

    # Some providers may send gzip even when the client did not explicitly
    # request it.
    if raw.startswith(b"\x1f\x8b"):
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError) as exc:
            raise WatcherError("The compressed website response is invalid.") from exc

        if len(raw) > MAX_DOWNLOAD_BYTES:
            raise WatcherError("The decompressed page is too large.")

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WatcherError("The website response is not UTF-8.") from exc


def extract_next_data(html_text: str) -> dict[str, Any]:
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    if not match:
        raise WatcherError(
            "Could not find __NEXT_DATA__ on the doctor page. "
            "The website structure may have changed."
        )

    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise WatcherError("Could not parse __NEXT_DATA__.") from exc

    if not isinstance(value, dict):
        raise WatcherError("__NEXT_DATA__ has an unexpected structure.")

    return value


def find_doctor_name(value: Any) -> str | None:
    """Recursively search common doctor-name fields in page data."""
    preferred_keys = ("fullName", "full_name", "doctorName", "doctor_name")

    def walk(node: Any) -> str | None:
        if isinstance(node, dict):
            for key in preferred_keys:
                candidate = node.get(key)
                if isinstance(candidate, str):
                    candidate = candidate.strip()
                    if candidate and ("دکتر" in candidate or len(candidate) >= 3):
                        return candidate

            for key in ("doctor", "data", "items"):
                if key in node:
                    found = walk(node[key])
                    if found:
                        return found

            for child in node.values():
                found = walk(child)
                if found:
                    return found

        elif isinstance(node, list):
            for child in node:
                found = walk(child)
                if found:
                    return found

        return None

    return walk(value)


# Provider-neutral appointment item: every value is a string.
# Keys: id, place, date, time, datetime.
AppointmentItem = dict[str, str]


class ProviderSpec(NamedTuple):
    """Minimal contract every appointment provider adapter fulfils.

    normalize_url        validate a doctor URL and return its canonical form
    extract_doctor_id    pull the provider's doctor identifier from the URL
    detect_name          best-effort doctor display name from the page
    fetch_open_appointments(url, doctor_id) -> list[AppointmentItem]
    """

    name: str
    hosts: tuple[str, ...]
    normalize_url: Callable[[str], str]
    extract_doctor_id: Callable[[str], str]
    detect_name: Callable[[str], str | None]
    fetch_open_appointments: Callable[[str, str], list[AppointmentItem]]
