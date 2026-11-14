"""Tests for the provider-neutral appointment model."""

from __future__ import annotations

import unittest

from providers.model import (
    Appointment,
    dedupe_appointments,
    describe_item,
    earliest_slot,
    normalize_items,
    signature_for_items,
)


def make(**overrides) -> Appointment:
    defaults = dict(
        provider="doctoreto",
        doctor_id="d1",
        doctor_name="دکتر تست",
        appointment_id="a1",
        clinic="مطب",
        date="1405/08/20",
        time="10:30",
        datetime_iso="2026-11-11T10:30:00",
        booking_url="https://doctoreto.com/doctor/tehran/d1",
        price=None,
        checked_at="2026-11-13T10:00:00+03:30",
    )
    defaults.update(overrides)
    return Appointment(**defaults)


class TestNormalizeItems(unittest.TestCase):
    _items = [
        {"id": "7", "place": "مطب مرکزی", "date": "1405/08/20",
         "time": "10:30", "datetime": "2026-11-11T10:30:00"},
    ]

    def test_maps_all_fields(self) -> None:
        result = normalize_items(
            "doctoreto", "d1", "دکتر تست",
            "https://doctoreto.com/doctor/tehran/d1", self._items, "2026-11-13",
        )

        item = result[0]
        self.assertEqual(item.provider, "doctoreto")
        self.assertEqual(item.doctor_id, "d1")
        self.assertEqual(item.doctor_name, "دکتر تست")
        self.assertEqual(item.appointment_id, "7")
        self.assertEqual(item.clinic, "مطب مرکزی")
        self.assertEqual(item.date, "1405/08/20")
        self.assertEqual(item.time, "10:30")
        self.assertEqual(item.datetime_iso, "2026-11-11T10:30:00")
        self.assertEqual(item.checked_at, "2026-11-13")

    def test_unknown_price_stays_unknown_not_zero(self) -> None:
        result = normalize_items("doctoreto", "d1", "n", "u", self._items, "t")

        self.assertIsNone(result[0].price)

    def test_known_price_is_kept(self) -> None:
        items = [{**self._items[0], "price": "۵۰۰٬۰۰۰"}]

        result = normalize_items("doctoreto", "d1", "n", "u", items, "t")

        self.assertEqual(result[0].price, "۵۰۰٬۰۰۰")

    def test_missing_datetime_stays_none(self) -> None:
        items = [{k: v for k, v in self._items[0].items() if k != "datetime"}]

        result = normalize_items("doctoreto", "d1", "n", "u", items, "t")

        self.assertIsNone(result[0].datetime_iso)

    def test_booking_url_falls_back_to_doctor_page(self) -> None:
        result = normalize_items(
            "doctoreto", "d1", "n",
            "https://doctoreto.com/doctor/tehran/d1", self._items, "t",
        )

        self.assertEqual(
            result[0].booking_url, "https://doctoreto.com/doctor/tehran/d1"
        )

    def test_item_level_url_wins(self) -> None:
        items = [{**self._items[0], "url": "https://doctoreto.com/doctor/tehran/d1/slot"}]

        result = normalize_items(
            "doctoreto", "d1", "n",
            "https://doctoreto.com/doctor/tehran/d1", items, "t",
        )

        self.assertEqual(
            result[0].booking_url, "https://doctoreto.com/doctor/tehran/d1/slot"
        )

    def test_non_dict_entries_skipped(self) -> None:
        result = normalize_items("doctoreto", "d1", "n", "u",
                                 ["junk", self._items[0]], "t")  # type: ignore[list-item]

        self.assertEqual(len(result), 1)


class TestDedup(unittest.TestCase):
    def test_duplicates_removed_within_provider(self) -> None:
        items = [make(), make()]

        self.assertEqual(len(dedupe_appointments(items)), 1)

    def test_same_id_different_provider_kept(self) -> None:
        items = [make(), make(provider="paziresh24")]

        self.assertEqual(len(dedupe_appointments(items)), 2)

    def test_key_falls_back_to_date_time_without_datetime(self) -> None:
        first = make(datetime_iso=None)
        second = make(datetime_iso=None)

        self.assertEqual(len(dedupe_appointments([first, second])), 1)

        different = make(datetime_iso=None, time="11:00")
        self.assertEqual(
            len(dedupe_appointments([first, different])), 2
        )


class TestFingerprint(unittest.TestCase):
    def test_ignores_checked_at(self) -> None:
        earlier = make(checked_at="2026-11-13T10:00:00+03:30")
        later = make(checked_at="2026-11-13T20:00:00+03:30")

        self.assertEqual(earlier.fingerprint(), later.fingerprint())
        self.assertEqual(signature_for_items([earlier]), signature_for_items([later]))

    def test_changes_when_slot_changes(self) -> None:
        self.assertNotEqual(
            signature_for_items([make()]),
            signature_for_items([make(time="11:00")]),
        )

    def test_order_independent(self) -> None:
        first = make(appointment_id="a1")
        second = make(appointment_id="a2")

        self.assertEqual(
            signature_for_items([first, second]),
            signature_for_items([second, first]),
        )


class TestHelpers(unittest.TestCase):
    def test_earliest_slot_prefers_min_datetime(self) -> None:
        items = [
            make(appointment_id="late", datetime_iso="2026-11-12T09:00:00"),
            make(appointment_id="early", datetime_iso="2026-11-11T08:00:00"),
            make(appointment_id="nodate", datetime_iso=None),
        ]

        self.assertEqual(earliest_slot(items).appointment_id, "early")

    def test_earliest_slot_none_without_datetimes(self) -> None:
        self.assertIsNone(earliest_slot([make(datetime_iso=None)]))
        self.assertIsNone(earliest_slot([]))

    def test_describe_item_with_price(self) -> None:
        text = describe_item(make(price="۵۰۰٬۰۰۰"))

        self.assertIn("مطب | 1405/08/20 10:30", text)
        self.assertIn("قیمت: ۵۰۰٬۰۰۰", text)

    def test_describe_item_without_price(self) -> None:
        self.assertEqual(describe_item(make()), "مطب | 1405/08/20 10:30")

    def test_describe_item_with_missing_date(self) -> None:
        self.assertEqual(describe_item(make(date="", time="10:30")), "مطب | 10:30")


if __name__ == "__main__":
    unittest.main()
