"""Tests for the Paziresh24 provider adapter.

Parsing tests use synthetic page fixtures (tests/fixtures/) to verify
the provider adapter's expected page-data structure.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from providers import paziresh24
from providers.base import WatcherError


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
BOOKABLE_HTML = (FIXTURE_DIR / "paziresh24_bookable.html").read_text(encoding="utf-8")
UNAVAILABLE_HTML = (FIXTURE_DIR / "paziresh24_unavailable.html").read_text(
    encoding="utf-8"
)

SLUG = "doctor-sample-123"
DOCTOR_URL = f"https://paziresh24.com/dr/{SLUG}"


def make_page(saman_data: dict) -> str:
    payload = {
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "queries": [
                        {
                            "queryKey": [
                                "samanDoctorAvailabilityStatus",
                                SLUG,
                            ],
                            "state": {"data": saman_data},
                        }
                    ]
                }
            }
        }
    }

    return (
        '<html><script id="__NEXT_DATA__">'
        f"{json.dumps(payload, ensure_ascii=False)}"
        "</script></html>"
    )


class TestNormalizeDoctorUrl(unittest.TestCase):
    def test_accepts_http_and_https_and_www(self) -> None:
        for base in ("http://paziresh24.com", "https://paziresh24.com",
                     "https://www.paziresh24.com"):
            url = f"{base}/dr/{SLUG}"
            self.assertEqual(paziresh24.normalize_doctor_url(url), url)

    def test_strips_query_fragment_and_trailing_slash(self) -> None:
        url = f"https://paziresh24.com/dr/{SLUG}/?x=1#top"
        self.assertEqual(
            paziresh24.normalize_doctor_url(url), DOCTOR_URL
        )

    def test_rejects_non_http_scheme(self) -> None:
        with self.assertRaises(WatcherError) as ctx:
            paziresh24.normalize_doctor_url(f"ftp://paziresh24.com/dr/{SLUG}")

        self.assertIn("must start with http", str(ctx.exception))

    def test_rejects_other_hosts(self) -> None:
        with self.assertRaises(WatcherError) as ctx:
            paziresh24.normalize_doctor_url(f"https://example.com/dr/{SLUG}")

        self.assertIn("Please provide a paziresh24.com doctor URL", str(ctx.exception))

    def test_rejects_missing_dr_path(self) -> None:
        with self.assertRaises(WatcherError) as ctx:
            paziresh24.normalize_doctor_url("https://paziresh24.com/search/x")

        self.assertIn("does not look like a Paziresh24 doctor URL", str(ctx.exception))


class TestExtractDoctorId(unittest.TestCase):
    def test_extracts_percent_encoded_persian_slug(self) -> None:
        from urllib.parse import quote

        url = f"https://paziresh24.com/dr/{quote(SLUG)}"

        self.assertEqual(paziresh24.extract_doctor_id(url), SLUG)

    def test_extracts_already_decoded_slug(self) -> None:
        self.assertEqual(paziresh24.extract_doctor_id(DOCTOR_URL), SLUG)

    def test_numeric_disambiguation_suffix_kept(self) -> None:
        self.assertEqual(
            paziresh24.extract_doctor_id("https://paziresh24.com/dr/doctor-sample-3"),
            "doctor-sample-3",
        )

    def test_missing_slug_raises(self) -> None:
        with self.assertRaises(WatcherError) as ctx:
            paziresh24.extract_doctor_id("https://paziresh24.com/dr/")

        self.assertIn("Could not extract the doctor ID", str(ctx.exception))


class TestDetectName(unittest.TestCase):
    def test_display_name_from_synthetic_fixture(self) -> None:
        with mock.patch.object(paziresh24, "fetch_html", return_value=BOOKABLE_HTML):
            self.assertEqual(paziresh24.detect_doctor_name(DOCTOR_URL), "پزشک نمونه")

    def test_none_when_information_missing(self) -> None:
        self.assertIsNone(
            paziresh24.extract_doctor_name({"props": {"pageProps": {}}})
        )


class TestExtractOpenAppointments(unittest.TestCase):
    def test_bookable_page_yields_both_centers(self) -> None:
        items = paziresh24.extract_open_appointments(BOOKABLE_HTML, SLUG)

        self.assertEqual(len(items), 2)

        clinic = items[0]
        self.assertEqual(clinic["id"], "clinic-sample-001")
        self.assertEqual(clinic["place"], "مطب نمونه")
        self.assertEqual(clinic["date"], "2026-12-01")
        self.assertEqual(clinic["time"], "10:30")
        self.assertEqual(clinic["datetime"], "2026-12-01T10:30:00.000+03:30")

        online = items[1]
        self.assertEqual(online["id"], "online-sample-002")
        self.assertEqual(online["place"], "ویزیت آنلاین نمونه")
        self.assertEqual(online["time"], "11:00")

    def test_unavailable_page_yields_nothing(self) -> None:
        items = paziresh24.extract_open_appointments(
            UNAVAILABLE_HTML, SLUG
        )

        self.assertEqual(items, [])

    def test_wrong_slug_raises(self) -> None:
        with self.assertRaises(WatcherError) as ctx:
            paziresh24.extract_open_appointments(BOOKABLE_HTML, "someone-else")

        self.assertIn("Could not find the availability status", str(ctx.exception))

    def test_missing_saman_query_raises(self) -> None:
        html = (
            '<html><script id="__NEXT_DATA__">'
            '{"props": {"pageProps": {"dehydratedState": {"queries": []}}}}'
            "</script></html>"
        )

        with self.assertRaises(WatcherError):
            paziresh24.extract_open_appointments(html, SLUG)

    def test_available_without_slot_time_is_skipped(self) -> None:
        html = make_page(
            {
                "centers": [
                    {
                        "id": "c1",
                        "availability_status": {
                            "booking_available": True,
                            "nearest_time_slot": None,
                        },
                    }
                ]
            }
        )

        # booking_available=true but no reliable slot: must not fabricate one.
        self.assertEqual(paziresh24.extract_open_appointments(html, SLUG), [])

    def test_falls_back_to_doctor_level_channels(self) -> None:
        html = make_page(
            {
                "in_person_availability": {
                    "booking_available": True,
                    "nearest_time_slot": "2026-11-20T09:00:00.000+03:30",
                },
                "online_visit_availability": {"booking_available": False},
                "centers": [],
            }
        )

        items = paziresh24.extract_open_appointments(html, SLUG)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "in_person")
        self.assertEqual(items[0]["place"], "مطب")
        self.assertEqual(items[0]["time"], "09:00")

    def test_center_without_mapped_name_gets_fallback_place(self) -> None:
        html = make_page(
            {
                "centers": [
                    {
                        "id": "unknown-1",
                        "availability_status": {
                            "booking_available": True,
                            "nearest_time_slot": "2026-11-20T09:30:00.000+03:30",
                        },
                    }
                ]
            }
        )

        items = paziresh24.extract_open_appointments(html, SLUG)

        self.assertEqual(items[0]["place"], "مطب")


class TestSlotSplitting(unittest.TestCase):
    def test_iso_slot_with_milliseconds_and_offset(self) -> None:
        self.assertEqual(
            paziresh24._split_slot("2026-11-14T15:20:00.000+03:30"),
            ("2026-11-14", "15:20"),
        )

    def test_garbage_slot_returns_empty(self) -> None:
        self.assertEqual(paziresh24._split_slot("not-a-date"), ("", ""))
        self.assertEqual(paziresh24._split_slot(None), ("", ""))


class TestProviderWiring(unittest.TestCase):
    def test_spec_metadata_and_functions(self) -> None:
        spec = paziresh24.PROVIDER

        self.assertEqual(spec.name, "paziresh24")
        self.assertIn("paziresh24.com", spec.hosts)
        self.assertIs(spec.normalize_url, paziresh24.normalize_doctor_url)
        self.assertIs(spec.extract_doctor_id, paziresh24.extract_doctor_id)
        self.assertIs(spec.detect_name, paziresh24.detect_doctor_name)
        self.assertIs(
            spec.fetch_open_appointments, paziresh24.fetch_open_appointments
        )

    def test_registered_in_registry(self) -> None:
        from providers import REGISTRY

        self.assertIn("paziresh24", REGISTRY)


class TestFetchOpenAppointments(unittest.TestCase):
    def test_fetches_then_parses(self) -> None:
        with mock.patch.object(paziresh24, "fetch_html", return_value=BOOKABLE_HTML):
            items = paziresh24.fetch_open_appointments(DOCTOR_URL, SLUG)

        self.assertEqual(len(items), 2)


if __name__ == "__main__":
    unittest.main()
