"""Tests for the Doctoreto provider adapter (providers/doctoreto.py)."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from providers import doctoreto
from providers.base import WatcherError


DOCTOR_ID = "doctor-sample-456"
DOCTOR_URL = f"https://doctoreto.com/doctor/tehran/{DOCTOR_ID}"


def make_page(consultations: list[dict], doctor_id: str = DOCTOR_ID) -> str:
    """Build a synthetic Doctoreto doctor page around __NEXT_DATA__."""
    next_data = {
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "queries": [
                        {
                            "queryKey": ["consultations", "doctors", doctor_id],
                            "state": {"data": consultations},
                        },
                        {
                            "queryKey": ["other", "query"],
                            "state": {"data": []},
                        },
                    ]
                }
            }
        }
    }

    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(next_data, ensure_ascii=False)}"
        "</script></html>"
    )


AVAILABLE_CONSULTATION = {
    "id": 7,
    "place": {"name": "مطب مرکزی"},
    "nextFreeTime": {
        "isAvailable": True,
        "shamsiDate": "1405/08/20",
        "shamsiTime": "10:30",
        "datetime": "2026-11-11T10:30:00",
    },
}


class TestNormalizeDoctorUrl(unittest.TestCase):
    def test_accepts_http_and_https(self) -> None:
        for scheme in ("http", "https"):
            url = f"{scheme}://doctoreto.com/doctor/tehran/{DOCTOR_ID}"
            self.assertEqual(doctoreto.normalize_doctor_url(url), url)

    def test_accepts_www_host(self) -> None:
        url = f"https://www.doctoreto.com/doctor/tehran/{DOCTOR_ID}"
        self.assertEqual(doctoreto.normalize_doctor_url(url), url)

    def test_strips_query_fragment_and_trailing_slash(self) -> None:
        url = f"https://doctoreto.com/doctor/tehran/{DOCTOR_ID}/?x=1#top"
        self.assertEqual(
            doctoreto.normalize_doctor_url(url),
            f"https://doctoreto.com/doctor/tehran/{DOCTOR_ID}",
        )

    def test_rejects_non_http_scheme(self) -> None:
        with self.assertRaises(WatcherError) as ctx:
            doctoreto.normalize_doctor_url(f"ftp://{DOCTOR_URL[8:]}")

        self.assertIn("must start with http", str(ctx.exception))

    def test_rejects_other_hosts(self) -> None:
        with self.assertRaises(WatcherError) as ctx:
            doctoreto.normalize_doctor_url("https://example.com/doctor/tehran/x")

        self.assertIn("Please provide a doctoreto.com doctor URL", str(ctx.exception))

    def test_rejects_uppercase_host_legacy_behavior(self) -> None:
        # Pinned legacy behavior: the host check is case-sensitive.
        with self.assertRaises(WatcherError):
            doctoreto.normalize_doctor_url(f"https://DOCTORETO.COM/doctor/t/{DOCTOR_ID}")

    def test_rejects_missing_doctor_path(self) -> None:
        with self.assertRaises(WatcherError) as ctx:
            doctoreto.normalize_doctor_url("https://doctoreto.com/clinics/xyz")

        self.assertIn("does not look like a Doctoreto doctor URL", str(ctx.exception))


class TestExtractDoctorId(unittest.TestCase):
    def test_extracts_id_after_doctor_segment(self) -> None:
        self.assertEqual(doctoreto.extract_doctor_id(DOCTOR_URL), DOCTOR_ID)

    def test_extracts_numeric_id(self) -> None:
        self.assertEqual(
            doctoreto.extract_doctor_id("https://doctoreto.com/doctor/tehran/12345"),
            "12345",
        )

    def test_doctor_segment_without_intermediate_part_raises(self) -> None:
        # Pinned grammar: the URL must be /doctor/<segment>/<id>; the ID is
        # taken from two positions after "doctor".
        with self.assertRaises(WatcherError):
            doctoreto.extract_doctor_id("https://doctoreto.com/doctor/12345")

    def test_missing_id_raises(self) -> None:
        with self.assertRaises(WatcherError) as ctx:
            doctoreto.extract_doctor_id("https://doctoreto.com/doctor")

        self.assertIn("Could not extract the doctor ID", str(ctx.exception))

    def test_unexpected_characters_raise(self) -> None:
        with self.assertRaises(WatcherError) as ctx:
            doctoreto.extract_doctor_id(
                f"https://doctoreto.com/doctor/tehran/{DOCTOR_ID}$bad"
            )

        self.assertIn("unexpected characters", str(ctx.exception))


class TestExtractOpenAppointments(unittest.TestCase):
    def test_maps_available_consultation(self) -> None:
        html = make_page([AVAILABLE_CONSULTATION])

        items = doctoreto.extract_open_appointments(html, DOCTOR_ID)

        self.assertEqual(
            items,
            [
                {
                    "id": "7",
                    "place": "مطب مرکزی",
                    "date": "1405/08/20",
                    "time": "10:30",
                    "datetime": "2026-11-11T10:30:00",
                }
            ],
        )

    def test_skips_unavailable_consultation(self) -> None:
        html = make_page(
            [{**AVAILABLE_CONSULTATION, "nextFreeTime": {"isAvailable": False}}]
        )

        self.assertEqual(doctoreto.extract_open_appointments(html, DOCTOR_ID), [])

    def test_skips_missing_next_free_time(self) -> None:
        html = make_page([{"id": 1, "place": {"name": "x"}}])

        self.assertEqual(doctoreto.extract_open_appointments(html, DOCTOR_ID), [])

    def test_place_fallback_when_missing(self) -> None:
        consultation = {
            "id": 2,
            "nextFreeTime": {**AVAILABLE_CONSULTATION["nextFreeTime"]},
        }
        html = make_page([consultation])

        items = doctoreto.extract_open_appointments(html, DOCTOR_ID)

        self.assertEqual(items[0]["place"], "Clinic")

    def test_skips_non_dict_consultation(self) -> None:
        html = make_page(["junk", AVAILABLE_CONSULTATION])  # type: ignore[list-item]

        items = doctoreto.extract_open_appointments(html, DOCTOR_ID)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "7")

    def test_wrong_doctor_id_raises(self) -> None:
        html = make_page([AVAILABLE_CONSULTATION])

        with self.assertRaises(WatcherError) as ctx:
            doctoreto.extract_open_appointments(html, "someone-else")

        self.assertIn("Could not find the consultation list", str(ctx.exception))

    def test_missing_dehydrated_state_raises(self) -> None:
        html = (
            '<html><script id="__NEXT_DATA__">'
            '{"props": {"pageProps": {}}}'
            "</script></html>"
        )

        with self.assertRaises(WatcherError) as ctx:
            doctoreto.extract_open_appointments(html, DOCTOR_ID)

        self.assertIn("Could not locate the appointment data", str(ctx.exception))


class TestFetchOpenAppointments(unittest.TestCase):
    def test_fetches_then_parses(self) -> None:
        html = make_page([AVAILABLE_CONSULTATION])

        with mock.patch.object(doctoreto, "fetch_html", return_value=html) as fetch:
            items = doctoreto.fetch_open_appointments(DOCTOR_URL, DOCTOR_ID)

        fetch.assert_called_once_with(DOCTOR_URL)
        self.assertEqual(items[0]["id"], "7")


class TestProviderWiring(unittest.TestCase):
    def test_spec_metadata(self) -> None:
        spec = doctoreto.PROVIDER

        self.assertEqual(spec.name, "doctoreto")
        self.assertIn("doctoreto.com", spec.hosts)
        self.assertIn("www.doctoreto.com", spec.hosts)

    def test_spec_uses_module_functions(self) -> None:
        spec = doctoreto.PROVIDER

        self.assertIs(spec.normalize_url, doctoreto.normalize_doctor_url)
        self.assertIs(spec.extract_doctor_id, doctoreto.extract_doctor_id)
        self.assertIs(spec.detect_name, doctoreto.detect_doctor_name)
        self.assertIs(
            spec.fetch_open_appointments, doctoreto.fetch_open_appointments
        )


if __name__ == "__main__":
    unittest.main()
