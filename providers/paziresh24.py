"""Paziresh24 (paziresh24.com) provider adapter.

Data source (verified against live public doctor pages, 2026-11):

Paziresh24 doctor pages (https://www.paziresh24.com/dr/{slug}) are Next.js
pages embedding their state in <script id="__NEXT_DATA__">. Availability is
exposed in props.pageProps.dehydratedState.queries under the react-query key

    ["samanDoctorAvailabilityStatus", "<slug>"]

with the structure

    {
      "in_person_availability":  {"booking_available": bool,
                                  "nearest_time_slot": "ISO-8601 +03:30"| null,
                                  "nearest_available_time": ...},
      "online_visit_availability": {...},
      "centers": [{"id": "<center id>",
                   "availability_status": {"booking_available": bool,
                                           "nearest_time_slot": ...| null,
                                           "nearest_available_time": ...}}]
    }

Center IDs map to props.pageProps.centers[].id for the clinic name. The
per-center freeturn epoch on the page was cross-checked and matches
nearest_time_slot; the human-readable freeturn_text was observed to be stale
and is intentionally not used.

This adapter reads only data that is embedded in the public page. It does not
call any Paziresh24 API endpoint, does not authenticate, and never books.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlparse

from .base import (
    AppointmentItem,
    ProviderSpec,
    WatcherError,
    extract_next_data,
    fetch_html,
)


HOSTS = ("paziresh24.com", "www.paziresh24.com")

ONLINE_VISIT_PLACE = "ویزیت آنلاین پذیرش24"
IN_PERSON_PLACE = "مطب"

DOCTOR_PAGE_HINT = "/dr/"


def normalize_doctor_url(url: str) -> str:
    url = url.strip().split("#", 1)[0].split("?", 1)[0].rstrip("/")

    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise WatcherError("Doctor URL must start with http:// or https://.")

    if parsed.netloc not in HOSTS:
        raise WatcherError("Please provide a paziresh24.com doctor URL.")

    if DOCTOR_PAGE_HINT not in parsed.path:
        raise WatcherError("This does not look like a Paziresh24 doctor URL.")

    return url


def extract_doctor_id(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]

    try:
        dr_index = parts.index("dr")
    except ValueError as exc:
        raise WatcherError("Could not find the doctor section in the URL.") from exc

    if len(parts) <= dr_index + 1:
        raise WatcherError("Could not extract the doctor ID from the URL.")

    doctor_id = unquote(parts[dr_index + 1].strip())

    if not doctor_id or "/" in doctor_id:
        raise WatcherError("The extracted doctor ID contains unexpected characters.")

    return doctor_id


def extract_doctor_name(next_data: dict[str, Any]) -> str | None:
    information = next_data.get("props", {}).get("pageProps", {}).get("information")

    if not isinstance(information, dict):
        return None

    display_name = str(information.get("display_name", "")).strip()

    return display_name or None


def detect_doctor_name(doctor_url: str) -> str | None:
    html_text = fetch_html(doctor_url)
    return extract_doctor_name(extract_next_data(html_text))


def _split_slot(slot: Any) -> tuple[str, str]:
    """Split an ISO-8601 slot like 2026-11-14T15:20:00.000+03:30.

    Paziresh24 slots always carry the Tehran UTC offset, so plain string
    slicing is exact; no timezone conversion is attempted.
    """
    text = str(slot)
    date = text[:10]
    time = text[11:16]

    if not _looks_like_date(date) or not _looks_like_time(time):
        return "", ""

    return date, time


def _looks_like_date(value: str) -> bool:
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    return value[:4].isdigit() and value[5:7].isdigit() and value[8:10].isdigit()


def _looks_like_time(value: str) -> bool:
    if len(value) != 5 or value[2] != ":":
        return False
    return value[:2].isdigit() and value[3:5].isdigit()


def _center_names(next_data: dict[str, Any]) -> dict[str, str]:
    centers = (
        next_data.get("props", {}).get("pageProps", {}).get("centers")
    )

    names: dict[str, str] = {}

    if isinstance(centers, list):
        for center in centers:
            if isinstance(center, dict) and center.get("id"):
                names[str(center["id"])] = str(center.get("name") or "").strip()

    return names


def _item_from_status(
    item_id: str,
    place: str,
    status: Any,
) -> AppointmentItem | None:
    if not isinstance(status, dict):
        return None

    if status.get("booking_available") is not True:
        return None

    date, time = _split_slot(status.get("nearest_time_slot"))

    if not date:
        # Booking marked available without a reliable slot time; never
        # fabricate availability details.
        return None

    return {
        "id": item_id,
        "place": place or IN_PERSON_PLACE,
        "date": date,
        "time": time,
        "datetime": str(status.get("nearest_time_slot", "")),
    }


def extract_open_appointments(
    html_text: str,
    doctor_id: str,
) -> list[dict[str, str]]:
    next_data = extract_next_data(html_text)

    page_props = next_data.get("props", {}).get("pageProps")
    if not isinstance(page_props, dict):
        raise WatcherError(
            "Could not locate the appointment data in the page."
        )

    saman: Any = None

    queries = page_props.get("dehydratedState", {}).get("queries")
    if isinstance(queries, list):
        for query in queries:
            if not isinstance(query, dict):
                continue

            key = query.get("queryKey")

            if (
                isinstance(key, list)
                and len(key) >= 2
                and key[0] == "samanDoctorAvailabilityStatus"
                and key[1] == doctor_id
            ):
                candidate = query.get("state", {}).get("data")

                if isinstance(candidate, dict):
                    saman = candidate
                break

    if saman is None:
        raise WatcherError(
            "Could not find the availability status for this doctor. "
            "The website structure may have changed."
        )

    names = _center_names(next_data)
    open_items: list[dict[str, str]] = []

    centers = saman.get("centers")

    if isinstance(centers, list) and centers:
        for center in centers:
            if not isinstance(center, dict):
                continue

            item = _item_from_status(
                str(center.get("id", "")),
                names.get(str(center.get("id", "")), ""),
                center.get("availability_status"),
            )

            if item:
                open_items.append(item)

        return open_items

    # No per-center breakdown; fall back to the doctor-level channels.
    for item_id, place, channel in (
        ("in_person", IN_PERSON_PLACE, saman.get("in_person_availability")),
        ("online_visit", ONLINE_VISIT_PLACE, saman.get("online_visit_availability")),
    ):
        item = _item_from_status(item_id, place, channel)

        if item:
            open_items.append(item)

    return open_items


def fetch_open_appointments(doctor_url: str, doctor_id: str) -> list[AppointmentItem]:
    return extract_open_appointments(fetch_html(doctor_url), doctor_id)


PROVIDER = ProviderSpec(
    name="paziresh24",
    hosts=HOSTS,
    normalize_url=normalize_doctor_url,
    extract_doctor_id=extract_doctor_id,
    detect_name=detect_doctor_name,
    fetch_open_appointments=fetch_open_appointments,
)
