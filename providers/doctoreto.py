"""Doctoreto (doctoreto.com) provider adapter.

URL validation, doctor ID extraction and appointment parsing moved from
appointment_watcher.py (Phase 1 extraction); behavior is unchanged.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .base import (
    AppointmentItem,
    ProviderSpec,
    WatcherError,
    extract_next_data,
    fetch_html,
    find_doctor_name,
)


HOSTS = ("doctoreto.com", "www.doctoreto.com")


def normalize_doctor_url(url: str) -> str:
    url = url.strip().split("#", 1)[0].split("?", 1)[0].rstrip("/")

    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise WatcherError("Doctor URL must start with http:// or https://.")

    if parsed.netloc not in HOSTS:
        raise WatcherError("Please provide a doctoreto.com doctor URL.")

    if "/doctor/" not in parsed.path:
        raise WatcherError("This does not look like a Doctoreto doctor URL.")

    return url


def extract_doctor_id(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]

    try:
        doctor_index = parts.index("doctor")
    except ValueError as exc:
        raise WatcherError("Could not find the doctor section in the URL.") from exc

    if len(parts) <= doctor_index + 2:
        raise WatcherError("Could not extract the doctor ID from the URL.")

    doctor_id = parts[doctor_index + 2].strip()

    if not re.fullmatch(r"[A-Za-z0-9_-]+", doctor_id):
        raise WatcherError("The extracted doctor ID contains unexpected characters.")

    return doctor_id


def detect_doctor_name(doctor_url: str) -> str | None:
    html_text = fetch_html(doctor_url)
    next_data = extract_next_data(html_text)
    return find_doctor_name(next_data)


def extract_open_appointments(
    html_text: str,
    doctor_id: str,
) -> list[dict[str, str]]:
    next_data = extract_next_data(html_text)

    try:
        queries = next_data["props"]["pageProps"]["dehydratedState"]["queries"]
    except (KeyError, TypeError) as exc:
        raise WatcherError(
            "Could not locate the appointment data in the page."
        ) from exc

    consultations: list[dict[str, Any]] | None = None

    if isinstance(queries, list):
        for query in queries:
            if not isinstance(query, dict):
                continue

            key = query.get("queryKey")

            if (
                isinstance(key, list)
                and len(key) >= 3
                and key[:3] == ["consultations", "doctors", doctor_id]
            ):
                candidate = query.get("state", {}).get("data", [])

                if isinstance(candidate, list):
                    consultations = candidate
                    break

    if consultations is None:
        raise WatcherError(
            "Could not find the consultation list for this doctor. "
            "The website structure may have changed."
        )

    open_items: list[dict[str, str]] = []

    for consultation in consultations:
        if not isinstance(consultation, dict):
            continue

        next_time = consultation.get("nextFreeTime")

        if (
            not isinstance(next_time, dict)
            or next_time.get("isAvailable") is not True
        ):
            continue

        place = consultation.get("place", {})
        place_name = (
            place.get("name", "Clinic")
            if isinstance(place, dict)
            else "Clinic"
        )

        open_items.append(
            {
                "id": str(consultation.get("id", "")),
                "place": str(place_name),
                "date": str(next_time.get("shamsiDate", "")),
                "time": str(next_time.get("shamsiTime", "")),
                "datetime": str(next_time.get("datetime", "")),
            }
        )

    return open_items


def fetch_open_appointments(doctor_url: str, doctor_id: str) -> list[AppointmentItem]:
    return extract_open_appointments(fetch_html(doctor_url), doctor_id)


PROVIDER = ProviderSpec(
    name="doctoreto",
    hosts=HOSTS,
    normalize_url=normalize_doctor_url,
    extract_doctor_id=extract_doctor_id,
    detect_name=detect_doctor_name,
    fetch_open_appointments=fetch_open_appointments,
)
