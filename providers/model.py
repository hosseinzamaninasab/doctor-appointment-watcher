"""Provider-neutral appointment model and normalization.

Every provider adapter returns loose item dicts (id, place, date, time,
datetime, and optionally url/price). This module converts them into a typed
Appointment record that the comparison view, state fingerprints and Telegram
notifications all consume, so no provider-specific knowledge leaks past the
adapter boundary.

Rules:
- price is None when unknown; it is never defaulted to zero.
- datetime_iso is None unless the provider supplied a reliable value.
- deduplication keys on provider + appointment id + clinic + datetime, so the
  same slot seen twice within one check is reported once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class Appointment:
    provider: str
    doctor_id: str
    doctor_name: str
    appointment_id: str
    clinic: str
    date: str
    time: str
    datetime_iso: str | None
    booking_url: str
    price: str | None
    checked_at: str

    def state_fields(self) -> dict[str, str | None]:
        """Stable fields used for notification fingerprints.

        checked_at is deliberately excluded: re-checking the same slots must
        not look like new availability.
        """
        data = asdict(self)
        data.pop("checked_at")
        return data

    def fingerprint(self) -> str:
        return json.dumps(self.state_fields(), ensure_ascii=False, sort_keys=True)


def normalize_items(
    provider: str,
    doctor_id: str,
    doctor_name: str,
    doctor_url: str,
    items: list[dict[str, str]],
    checked_at: str,
) -> list[Appointment]:
    appointments: list[Appointment] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        datetime_iso = str(item.get("datetime", "")).strip() or None
        price_raw = str(item.get("price", "")).strip()

        appointments.append(
            Appointment(
                provider=provider,
                doctor_id=doctor_id,
                doctor_name=doctor_name,
                appointment_id=str(item.get("id", "")),
                clinic=str(item.get("place", "")),
                date=str(item.get("date", "")),
                time=str(item.get("time", "")),
                datetime_iso=datetime_iso,
                booking_url=str(item.get("url", "")).strip() or doctor_url,
                price=price_raw or None,
                checked_at=checked_at,
            )
        )

    return dedupe_appointments(appointments)


def dedupe_appointments(items: list[Appointment]) -> list[Appointment]:
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[Appointment] = []

    for item in items:
        key = (
            item.provider,
            item.appointment_id,
            item.clinic,
            item.datetime_iso or f"{item.date} {item.time}",
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(item)

    return unique


def earliest_slot(items: list[Appointment]) -> Appointment | None:
    """The appointment with the earliest known datetime, if any.

    Appointments without a parseable datetime sort last and never win.
    """
    dated = [item for item in items if item.datetime_iso]

    if not dated:
        return None

    return min(dated, key=lambda item: item.datetime_iso or "")


def to_display_dict(item: Appointment) -> dict[str, str | None]:
    return asdict(item)


def signature_for_items(items: list[Appointment]) -> str:
    """Order-independent fingerprint of a whole availability result."""
    return json.dumps(
        sorted(item.fingerprint() for item in items),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def describe_item(item: Appointment) -> str:
    when = " ".join(part for part in (item.date, item.time) if part)
    price = f" | قیمت: {item.price}" if item.price else ""
    return f"{item.clinic} | {when}{price}".strip(" |")
