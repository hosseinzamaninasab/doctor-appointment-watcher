"""Tests for CLI orchestration, config migration, state isolation and the
multi-provider watch loop."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import appointment_watcher as aw
from providers import doctoreto, paziresh24
from providers.base import WatcherError, WatcherHTTPError
from providers.model import normalize_items, signature_for_items


DOCTOR_ID = "doctor-sample-456"
DOCTOR_URL = f"https://doctoreto.com/doctor/tehran/{DOCTOR_ID}"
DOCTOR_NAME = "پزشک نمونه"

P24_ID = "doctor-sample-123"
P24_URL = f"https://paziresh24.com/dr/{P24_ID}"

BOT_TOKEN = "123:ABC"
CHAT_ID = "555"

# Legacy (v1) flat configuration, as written by Phase-1 and older versions.
LEGACY_CONFIG = {
    "doctor_name": DOCTOR_NAME,
    "doctor_id": DOCTOR_ID,
    "doctor_url": DOCTOR_URL,
    "provider": "doctoreto",
    "bot_token": BOT_TOKEN,
    "chat_id": CHAT_ID,
    "check_interval_seconds": 600,
}

DOCTORETO_TARGET = {
    "provider": "doctoreto",
    "doctor_name": DOCTOR_NAME,
    "doctor_id": DOCTOR_ID,
    "doctor_url": DOCTOR_URL,
}

P24_TARGET = {
    "provider": "paziresh24",
    "doctor_name": "پزشک نمونه",
    "doctor_id": P24_ID,
    "doctor_url": P24_URL,
}

FIXTURES = Path(__file__).resolve().parent / "fixtures"
P24_BOOKABLE_HTML = (FIXTURES / "paziresh24_bookable.html").read_text(encoding="utf-8")
P24_UNAVAILABLE_HTML = (
    FIXTURES / "paziresh24_unavailable.html"
).read_text(encoding="utf-8")


def make_next_data(consultations: list, doctor_id: str = DOCTOR_ID) -> str:
    import json as _json

    payload = {
        "props": {
            "pageProps": {
                "doctor": {"fullName": DOCTOR_NAME},
                "dehydratedState": {
                    "queries": [
                        {
                            "queryKey": ["consultations", "doctors", doctor_id],
                            "state": {"data": consultations},
                        }
                    ]
                },
            }
        }
    }

    return (
        '<html><script id="__NEXT_DATA__">'
        f"{_json.dumps(payload, ensure_ascii=False)}"
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

AVAILABLE_PAGE = make_next_data([AVAILABLE_CONSULTATION])
EMPTY_PAGE = make_next_data([])


def appt_items(
    target: dict,
    items: list[dict],
    checked_at: str = "2026-11-13T10:00:00+03:30",
):
    return normalize_items(
        target["provider"],
        target["doctor_id"],
        target["doctor_name"],
        target["doctor_url"],
        items,
        checked_at,
    )


class TempFilesTest(unittest.TestCase):
    """Redirects config/state files to a temp directory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp_path = Path(self._tmp.name)

        self.config_file = tmp_path / "config.json"
        self.state_file = tmp_path / "state.json"

        config_patch = mock.patch.object(aw, "CONFIG_FILE", self.config_file)
        state_patch = mock.patch.object(aw, "STATE_FILE", self.state_file)
        config_patch.start()
        state_patch.start()
        self.addCleanup(config_patch.stop)
        self.addCleanup(state_patch.stop)

    def write_config(self, config: dict) -> None:
        self.config_file.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_state(self) -> dict:
        return json.loads(self.state_file.read_text(encoding="utf-8"))


class TestLoadConfigCompat(TempFilesTest):
    def test_legacy_config_without_provider_infers_doctoreto(self) -> None:
        legacy = {k: v for k, v in LEGACY_CONFIG.items() if k != "provider"}
        self.write_config(legacy)

        config = aw.load_config()

        self.assertEqual(config["targets"][0]["provider"], "doctoreto")
        self.assertEqual(config["targets"][0]["doctor_id"], DOCTOR_ID)
        self.assertEqual(config["bot_token"], BOT_TOKEN)

    def test_legacy_config_with_explicit_provider_is_respected(self) -> None:
        self.write_config(LEGACY_CONFIG)

        self.assertEqual(aw.load_config()["targets"][0]["provider"], "doctoreto")

    def test_legacy_config_unknown_provider_raises(self) -> None:
        self.write_config({**LEGACY_CONFIG, "provider": "doesnotexist"})

        with self.assertRaises(WatcherError):
            aw.load_config()

    def test_v2_config_with_two_targets(self) -> None:
        self.write_config(
            {
                "version": 2,
                "bot_token": BOT_TOKEN,
                "chat_id": CHAT_ID,
                "check_interval_seconds": 300,
                "targets": [DOCTORETO_TARGET, P24_TARGET],
            }
        )

        config = aw.load_config()

        self.assertEqual(len(config["targets"]), 2)
        self.assertEqual(config["targets"][1]["provider"], "paziresh24")
        self.assertEqual(config["check_interval_seconds"], 300)

    def test_missing_token_raises_legacy_message(self) -> None:
        self.write_config({**LEGACY_CONFIG, "bot_token": ""})

        with self.assertRaises(WatcherError) as ctx:
            aw.load_config()

        self.assertIn("Configuration is incomplete", str(ctx.exception))

    def test_v2_target_missing_field_raises_specifically(self) -> None:
        broken = {**DOCTORETO_TARGET, "doctor_id": ""}
        self.write_config(
            {"version": 2, "bot_token": BOT_TOKEN, "chat_id": CHAT_ID,
             "targets": [broken]}
        )

        with self.assertRaises(WatcherError) as ctx:
            aw.load_config()

        self.assertIn("doctor_id", str(ctx.exception))

    def test_config_without_targets_raises(self) -> None:
        self.write_config({"bot_token": BOT_TOKEN, "chat_id": CHAT_ID})

        with self.assertRaises(WatcherError):
            aw.load_config()

    def test_interval_clamped_and_sanitized(self) -> None:
        for raw, expected in ((10, 600), ("abc", 600), (120, 120)):
            self.write_config({**LEGACY_CONFIG, "check_interval_seconds": raw})
            self.assertEqual(aw.load_config()["check_interval_seconds"], expected)

    def test_config_exists_for_legacy_config(self) -> None:
        self.assertFalse(aw.config_exists())
        self.write_config(LEGACY_CONFIG)
        self.assertTrue(aw.config_exists())


class TestStateMigration(TempFilesTest):
    def test_legacy_flat_state_attaches_to_first_target(self) -> None:
        legacy_state = {
            "last_alert_signature": "abc123",
            "consecutive_errors": 3,
            "error_alerted": True,
        }
        self.state_file.write_text(json.dumps(legacy_state), encoding="utf-8")

        state = aw.load_state({"targets": [DOCTORETO_TARGET]})

        entry = state["targets"][f"doctoreto:{DOCTOR_ID}"]
        self.assertEqual(entry["last_alert_signature"], "abc123")
        self.assertEqual(entry["consecutive_errors"], 3)
        self.assertTrue(entry["error_alerted"])

    def test_v2_state_passes_through(self) -> None:
        v2 = {
            "version": 2,
            "targets": {f"paziresh24:{P24_ID}": {"consecutive_errors": 2}},
        }
        self.state_file.write_text(json.dumps(v2, ensure_ascii=False), encoding="utf-8")

        state = aw.load_state({"targets": [DOCTORETO_TARGET]})

        self.assertEqual(
            state["targets"][f"paziresh24:{P24_ID}"]["consecutive_errors"], 2
        )

    def test_corrupt_error_count_defaults_to_zero(self) -> None:
        self.state_file.write_text(json.dumps({"consecutive_errors": "bogus"}))

        state = aw.load_state({"targets": [DOCTORETO_TARGET]})

        self.assertEqual(
            state["targets"][f"doctoreto:{DOCTOR_ID}"]["consecutive_errors"], 0
        )

    def test_get_target_state_creates_default(self) -> None:
        state = {"version": 2, "targets": {}}

        entry = aw.get_target_state(state, "paziresh24:new-doctor")

        self.assertEqual(entry, aw.default_target_state())
        self.assertIn("paziresh24:new-doctor", state["targets"])


class TestCheckTarget(TempFilesTest):
    def test_available_sends_alert_and_stores_fingerprint(self) -> None:
        send = mock.Mock()
        config = {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}
        state = aw.load_state({"targets": [DOCTORETO_TARGET]})

        with (
            mock.patch.object(doctoreto, "fetch_html", return_value=AVAILABLE_PAGE),
            mock.patch.object(aw, "send_telegram", send),
        ):
            items = aw.check_target(config, DOCTORETO_TARGET, state)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].clinic, "مطب مرکزی")
        send.assert_called_once()
        self.assertIn("doctoreto", send.call_args[0][2])

        entry = state["targets"][f"doctoreto:{DOCTOR_ID}"]
        self.assertTrue(entry["last_alert_signature"])
        self.assertEqual(entry["consecutive_errors"], 0)
        self.assertIsNotNone(entry["last_success_at"])

    def test_same_availability_is_not_re_alerted(self) -> None:
        send = mock.Mock()
        config = {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}
        state = aw.load_state({"targets": [DOCTORETO_TARGET]})

        with (
            mock.patch.object(doctoreto, "fetch_html", return_value=AVAILABLE_PAGE),
            mock.patch.object(aw, "send_telegram", send),
        ):
            aw.check_target(config, DOCTORETO_TARGET, state)
            aw.check_target(config, DOCTORETO_TARGET, state)

        send.assert_called_once()

    def test_availability_then_nothing_then_available_re_alerts(self) -> None:
        send = mock.Mock()
        config = {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}
        state = aw.load_state({"targets": [DOCTORETO_TARGET]})

        with (
            mock.patch.object(
                doctoreto,
                "fetch_html",
                side_effect=[AVAILABLE_PAGE, EMPTY_PAGE, AVAILABLE_PAGE],
            ),
            mock.patch.object(aw, "send_telegram", send),
        ):
            aw.check_target(config, DOCTORETO_TARGET, state)
            aw.check_target(config, DOCTORETO_TARGET, state)
            aw.check_target(config, DOCTORETO_TARGET, state)

        self.assertEqual(send.call_count, 2)

    def test_fingerprint_ignores_check_timestamp(self) -> None:
        # Same slots checked later must produce the same fingerprint, so no
        # duplicate alert. This also covers legacy-state migration: a v1
        # fingerprint (without checked_at) suppresses the first alert.
        items_a = appt_items(DOCTORETO_TARGET, [
            {"id": "7", "place": "مطب", "date": "1405/08/20",
             "time": "10:30", "datetime": "2026-11-11T10:30:00"},
        ])
        items_b = appt_items(DOCTORETO_TARGET, [
            {"id": "7", "place": "مطب", "date": "1405/08/20",
             "time": "10:30", "datetime": "2026-11-11T10:30:00"},
        ], checked_at="2026-11-14T00:00:00+03:30")

        self.assertEqual(signature_for_items(items_a), signature_for_items(items_b))

    def test_legacy_fingerprint_suppresses_first_alert_after_upgrade(self) -> None:
        send = mock.Mock()
        config = {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}

        items = appt_items(DOCTORETO_TARGET, [
            {"id": "7", "place": "مطب مرکزی", "date": "1405/08/20",
             "time": "10:30", "datetime": "2026-11-11T10:30:00"},
        ])

        self.state_file.write_text(
            json.dumps({"last_alert_signature": signature_for_items(items)}),
            encoding="utf-8",
        )

        page = make_next_data([AVAILABLE_CONSULTATION])

        with (
            mock.patch.object(doctoreto, "fetch_html", return_value=page),
            mock.patch.object(aw, "send_telegram", send),
        ):
            state = aw.load_state({"targets": [DOCTORETO_TARGET]})
            aw.check_target(config, DOCTORETO_TARGET, state)

        send.assert_not_called()


class TestCheckAllPartialFailure(TempFilesTest):
    def _config(self) -> dict:
        return {
            "bot_token": BOT_TOKEN,
            "chat_id": CHAT_ID,
            "check_interval_seconds": 600,
            "targets": [DOCTORETO_TARGET, P24_TARGET],
        }

    def test_provider_error_does_not_lose_other_results(self) -> None:
        send = mock.Mock()
        config = self._config()

        with (
            mock.patch.object(doctoreto, "fetch_html", return_value=AVAILABLE_PAGE),
            mock.patch.object(
                paziresh24, "fetch_html",
                side_effect=WatcherError("Connection failed: boom"),
            ),
            mock.patch.object(aw, "send_telegram", send),
        ):
            results = aw.check_all(config)

        self.assertEqual(len(results), 2)
        self.assertEqual(len(results[0]["items"]), 1)      # doctoreto succeeded
        self.assertIsNotNone(results[1]["error"])          # paziresh24 failed
        self.assertEqual(results[1]["items"], [])

        # Alert went out only for the successful provider.
        send.assert_called_once()
        self.assertIn("doctoreto", send.call_args[0][2])

        state = self.read_state()
        self.assertEqual(state["targets"][f"doctoreto:{DOCTOR_ID}"]["consecutive_errors"], 0)
        self.assertEqual(state["targets"][f"paziresh24:{P24_ID}"]["consecutive_errors"], 1)

    def test_paziresh24_parsed_from_synthetic_fixture(self) -> None:
        send = mock.Mock()
        config = self._config()

        with (
            mock.patch.object(doctoreto, "fetch_html", return_value=EMPTY_PAGE),
            mock.patch.object(paziresh24, "fetch_html", return_value=P24_BOOKABLE_HTML),
            mock.patch.object(aw, "send_telegram", send),
        ):
            results = aw.check_all(config)

        items = results[1]["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].provider, "paziresh24")
        self.assertEqual(items[0].clinic, "مطب نمونه")
        self.assertEqual(items[0].time, "10:30")
        self.assertEqual(items[1].clinic, "ویزیت آنلاین نمونه")
        self.assertIsNone(items[0].price)  # price unknown, never zero
        send.assert_called_once()          # paziresh24 alert sent
        self.assertIn("paziresh24", send.call_args[0][2])

    def test_paziresh24_unavailable_fixture_yields_no_alert(self) -> None:
        send = mock.Mock()
        # The unavailable fixture is the captured page of a different doctor;
        # use a target whose slug matches that page.
        unavailable_target = {
            "provider": "paziresh24",
            "doctor_name": "پزشک نمونه",
            "doctor_id": "doctor-sample-123",
            "doctor_url": "https://paziresh24.com/dr/doctor-sample-123",
        }
        config = {
            "bot_token": BOT_TOKEN,
            "chat_id": CHAT_ID,
            "check_interval_seconds": 600,
            "targets": [DOCTORETO_TARGET, unavailable_target],
        }

        with (
            mock.patch.object(doctoreto, "fetch_html", return_value=EMPTY_PAGE),
            mock.patch.object(paziresh24, "fetch_html", return_value=P24_UNAVAILABLE_HTML),
            mock.patch.object(aw, "send_telegram", send),
        ):
            results = aw.check_all(config)

        self.assertEqual(results[1]["items"], [])
        self.assertIsNone(results[1]["error"])
        send.assert_not_called()


class TestStateIsolation(TempFilesTest):
    def test_one_doctor_fingerprint_does_not_suppress_another(self) -> None:
        send = mock.Mock()
        config = {
            "bot_token": BOT_TOKEN,
            "chat_id": CHAT_ID,
            "targets": [
                DOCTORETO_TARGET,
                {**DOCTORETO_TARGET, "doctor_id": "dr-two", "doctor_name": "دکتر دوم",
                 "doctor_url": "https://doctoreto.com/doctor/tehran/dr-two"},
            ],
        }
        page_two = make_next_data([AVAILABLE_CONSULTATION], doctor_id="dr-two")

        with (
            mock.patch.object(
                doctoreto, "fetch_html", side_effect=[AVAILABLE_PAGE, page_two]
            ),
            mock.patch.object(aw, "send_telegram", send),
        ):
            results = aw.check_all(config)

        self.assertEqual(len(results[0]["items"]), 1)
        self.assertEqual(len(results[1]["items"]), 1)
        self.assertEqual(send.call_count, 2)  # both alerted independently

    def test_doctor_a_clearing_does_not_reset_doctor_b(self) -> None:
        send = mock.Mock()
        target_b = {**DOCTORETO_TARGET, "doctor_id": "dr-two", "doctor_name": "دکتر دوم",
                    "doctor_url": "https://doctoreto.com/doctor/tehran/dr-two"}
        config = {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID,
                  "targets": [DOCTORETO_TARGET, target_b]}
        page_two = make_next_data([AVAILABLE_CONSULTATION], doctor_id="dr-two")

        with (
            mock.patch.object(
                doctoreto,
                "fetch_html",
                side_effect=[
                    AVAILABLE_PAGE, page_two,     # run 1: both available
                    EMPTY_PAGE, page_two,         # run 2: A gone, B unchanged
                    AVAILABLE_PAGE, page_two,     # run 3: A back
                ],
            ),
            mock.patch.object(aw, "send_telegram", send),
        ):
            aw.check_all(config)  # 2 alerts
            aw.check_all(config)  # B unchanged: no alert
            aw.check_all(config)  # A back: 1 new alert

        self.assertEqual(send.call_count, 3)


class TestComparisonOutput(unittest.TestCase):
    def _results(self) -> list[dict]:
        target = DOCTORETO_TARGET
        items = appt_items(target, [
            {"id": "7", "place": "مطب مرکزی", "date": "1405/08/20",
             "time": "10:30", "datetime": "2026-11-11T10:30:00"},
        ])

        return [
            {"target": target, "items": items, "error": None},
            {"target": P24_TARGET, "items": [], "error": None},
            {"target": {**DOCTORETO_TARGET, "doctor_id": "x"},
             "items": [], "error": WatcherError("Connection failed: down")},
        ]

    def test_lines_cover_all_outcomes(self) -> None:
        lines = aw.comparison_lines(self._results(), "2026-11-13 10:00")
        text = "\n".join(lines)

        self.assertIn("Appointment comparison (2026-11-13 10:00)", text)
        self.assertIn("doctoreto — پزشک نمونه", text)
        self.assertIn("✅ 1 open slot(s)", text)
        self.assertIn("مطب مرکزی | 1405/08/20 10:30", text)
        self.assertIn(f"Book on the official site: {DOCTOR_URL}", text)
        self.assertIn("paziresh24 — پزشک نمونه", text)
        self.assertIn("❌ No open appointments", text)
        self.assertIn("⚠️ Check failed: Connection failed: down", text)

    def test_price_shown_only_when_known(self) -> None:
        target = DOCTORETO_TARGET
        priced = appt_items(target, [
            {"id": "7", "place": "مطب", "date": "1405/08/20", "time": "10:30",
             "datetime": "x", "price": "۵۰۰٬۰۰۰"},
        ])
        unpriced = appt_items(target, [
            {"id": "8", "place": "مطب", "date": "1405/08/20", "time": "11:30",
             "datetime": "y"},
        ])

        lines = aw.comparison_lines(
            [{"target": target, "items": priced + unpriced, "error": None}],
            "now",
        )
        text = "\n".join(lines)

        self.assertIn("قیمت: ۵۰۰٬۰۰۰", text)
        self.assertIn("مطب | 1405/08/20 11:30\n", text)


class TestBuildAlert(unittest.TestCase):
    def test_alert_contains_provider_doctor_slots_and_link(self) -> None:
        items = appt_items(DOCTORETO_TARGET, [
            {"id": "7", "place": "مطب مرکزی", "date": "1405/08/20",
             "time": "10:30", "datetime": "2026-11-11T10:30:00"},
        ])

        alert = aw.build_alert(DOCTORETO_TARGET, items)

        self.assertIn("🚨 نوبت پزشک نمونه (doctoreto) باز شد!", alert)
        self.assertIn("مطب: مطب مرکزی", alert)
        self.assertIn("اولین زمان آزاد: 1405/08/20 | 10:30", alert)
        self.assertIn(DOCTOR_URL, alert)


class TestTelegram429(unittest.TestCase):
    def _ok(self) -> dict:
        return {"ok": True, "result": {}}

    def test_retry_after_429_then_success(self) -> None:
        body = json.dumps({"ok": False, "parameters": {"retry_after": 2}}).encode()

        with (
            mock.patch.object(
                aw, "request_json",
                side_effect=[
                    WatcherHTTPError("rate limited", status=429, body=body),
                    self._ok(),
                ],
            ),
            mock.patch.object(aw.time, "sleep") as sleep,
        ):
            result = aw.telegram_api("t", "getMe", {})

        self.assertTrue(result["ok"])
        sleep.assert_called_once_with(2)

    def test_huge_retry_after_raises_instead_of_blocking(self) -> None:
        body = json.dumps({"parameters": {"retry_after": 500}}).encode()

        with (
            mock.patch.object(
                aw, "request_json",
                side_effect=WatcherHTTPError("rate limited", status=429, body=body),
            ),
            mock.patch.object(aw.time, "sleep") as sleep,
        ):
            with self.assertRaises(WatcherHTTPError):
                aw.telegram_api("t", "getMe", {})

        sleep.assert_not_called()

    def test_second_consecutive_429_raises(self) -> None:
        body = json.dumps({"parameters": {"retry_after": 1}}).encode()

        with (
            mock.patch.object(
                aw, "request_json",
                side_effect=[
                    WatcherHTTPError("rate limited", status=429, body=body),
                    WatcherHTTPError("rate limited", status=429, body=body),
                ],
            ),
            mock.patch.object(aw.time, "sleep"),
        ):
            with self.assertRaises(WatcherHTTPError):
                aw.telegram_api("t", "getMe", {})

    def test_other_http_errors_propagate_immediately(self) -> None:
        with (
            mock.patch.object(
                aw, "request_json",
                side_effect=WatcherHTTPError("nope", status=400, body=b"{}"),
            ),
            mock.patch.object(aw.time, "sleep") as sleep,
        ):
            with self.assertRaises(WatcherHTTPError):
                aw.telegram_api("t", "getMe", {})

        sleep.assert_not_called()

    def test_retry_after_parsing(self) -> None:
        parse = aw._telegram_retry_after

        self.assertEqual(
            parse(json.dumps({"parameters": {"retry_after": 7}}).encode()), 7
        )
        self.assertIsNone(parse(b"not json"))
        self.assertIsNone(parse(None))
        self.assertIsNone(parse(json.dumps({"parameters": {"retry_after": 0}}).encode()))
        self.assertIsNone(parse(json.dumps({"parameters": {}}).encode()))


class TestRecordErrorEscalation(TempFilesTest):
    def _record(self, config: dict, target: dict, state: dict, times: int) -> None:
        for _ in range(times):
            aw.record_target_error(config, target, WatcherError("boom"), state)

    def test_warning_sent_once_at_six_consecutive_errors(self) -> None:
        send = mock.Mock()
        config = {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}
        state = {"version": 2, "targets": {}}

        with mock.patch.object(aw, "send_telegram", send):
            self._record(config, DOCTORETO_TARGET, state, 5)
            send.assert_not_called()  # below the one-hour threshold

            self._record(config, DOCTORETO_TARGET, state, 1)  # 6th
            send.assert_called_once()
            self.assertIn("پایش نوبت", send.call_args[0][2])

            self._record(config, DOCTORETO_TARGET, state, 2)  # latch suppresses
            self.assertEqual(send.call_count, 1)

        entry = state["targets"][f"doctoreto:{DOCTOR_ID}"]
        self.assertTrue(entry["error_alerted"])
        self.assertEqual(entry["consecutive_errors"], 8)

    def test_telegram_failure_during_warning_is_swallowed(self) -> None:
        send = mock.Mock(side_effect=WatcherError("telegram down"))
        config = {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}
        state = {"version": 2, "targets": {}}

        with mock.patch.object(aw, "send_telegram", send):
            self._record(config, DOCTORETO_TARGET, state, 6)  # must not raise

        entry = state["targets"][f"doctoreto:{DOCTOR_ID}"]
        self.assertFalse(entry["error_alerted"])  # retry on the next round
        self.assertEqual(entry["consecutive_errors"], 6)


class TestChooseTelegramChat(TempFilesTest):
    @staticmethod
    def _updates(*chat_ids: int) -> dict:
        return {
            "ok": True,
            "result": [
                {"message": {"chat": {"id": cid, "first_name": f"User{cid}"}}}
                for cid in chat_ids
            ],
        }

    def test_single_chat_selected_without_prompt(self) -> None:
        with (
            mock.patch.object(aw, "telegram_api", return_value=self._updates(111)),
            mock.patch("builtins.input") as inp,
        ):
            self.assertEqual(aw.choose_telegram_chat("t"), "111")
        inp.assert_called_once()  # only the "press Enter" prompt

    def test_multiple_chats_prompted_and_second_selected(self) -> None:
        # getUpdates returns oldest-first; the code reverses it so the newest
        # chat is listed first: 1) 222, 2) 111.
        with (
            mock.patch.object(aw, "telegram_api", return_value=self._updates(111, 222)),
            mock.patch("builtins.input", side_effect=["", "2"]),
        ):
            self.assertEqual(aw.choose_telegram_chat("t"), "111")

    def test_multiple_chats_default_selects_newest(self) -> None:
        with (
            mock.patch.object(aw, "telegram_api", return_value=self._updates(111, 222)),
            mock.patch("builtins.input", side_effect=["", ""]),
        ):
            self.assertEqual(aw.choose_telegram_chat("t"), "222")

    def test_invalid_selection_raises(self) -> None:
        with (
            mock.patch.object(aw, "telegram_api", return_value=self._updates(111, 222)),
            mock.patch("builtins.input", side_effect=["", "99"]),
        ):
            with self.assertRaises(WatcherError):
                aw.choose_telegram_chat("t")

    def test_no_chats_raises(self) -> None:
        with (
            mock.patch.object(aw, "telegram_api", return_value={"ok": True, "result": []}),
            mock.patch("builtins.input", return_value=""),
        ):
            with self.assertRaises(WatcherError) as ctx:
                aw.choose_telegram_chat("t")

        self.assertIn("No Telegram chat was found", str(ctx.exception))


class MainCliTest(TempFilesTest):
    """main() CLI paths: --test, --once, menu, exit codes."""

    def _run_main(self, *argv: str) -> tuple[int, str]:
        stdout = io.StringIO()
        argv_patched = mock.patch("sys.argv", ["appointment_watcher.py", *argv])
        out_patched = mock.patch("sys.stdout", stdout)

        with argv_patched, out_patched:
            code = aw.main()

        return code, stdout.getvalue()

    def setUp(self) -> None:
        super().setUp()
        self.write_config(LEGACY_CONFIG)

    def test_flag_test_sends_message_with_target_list(self) -> None:
        send = mock.Mock()

        with mock.patch.object(aw, "send_telegram", send):
            code, out = self._run_main("--test")

        self.assertEqual(code, 0)
        self.assertIn("Test message sent", out)
        self.assertIn("doctoreto", send.call_args[0][2])

    def test_flag_test_without_config_fails_cleanly(self) -> None:
        self.config_file.unlink()

        code, _ = self._run_main("--test")

        self.assertEqual(code, 1)

    def test_flag_once_prints_comparison(self) -> None:
        with (
            mock.patch.object(doctoreto, "fetch_html", return_value=AVAILABLE_PAGE),
            mock.patch.object(aw, "send_telegram"),
        ):
            code, out = self._run_main("--once")

        self.assertEqual(code, 0)
        self.assertIn("Appointment comparison", out)
        self.assertIn("doctoreto — پزشک نمونه", out)
        self.assertIn("✅ 1 open slot(s)", out)

        state = self.read_state()
        self.assertTrue(
            state["targets"][f"doctoreto:{DOCTOR_ID}"]["last_alert_signature"]
        )

    def test_menu_exit_option(self) -> None:
        with mock.patch("builtins.input", return_value="3"):
            code, out = self._run_main()

        self.assertEqual(code, 0)
        self.assertIn("Goodbye", out)

    def test_menu_invalid_choice_fails_cleanly(self) -> None:
        with mock.patch("builtins.input", return_value="9"):
            code, _ = self._run_main()

        self.assertEqual(code, 1)

    def test_menu_watch_loop_stops_on_keyboard_interrupt(self) -> None:
        sleeps = mock.Mock()
        checks = mock.Mock(
            side_effect=[[{"target": DOCTORETO_TARGET, "items": [], "error": None}],
                         KeyboardInterrupt]
        )

        with (
            mock.patch("builtins.input", return_value="1"),
            mock.patch.object(aw, "check_all", checks),
            mock.patch.object(aw.time, "sleep", sleeps),
        ):
            code, out = self._run_main()

        self.assertEqual(code, 0)
        self.assertIn("Watcher stopped", out)
        self.assertEqual(checks.call_count, 2)
        self.assertEqual(sleeps.call_count, 1)

    def test_menu_watch_loop_survives_check_errors(self) -> None:
        sleeps = mock.Mock()
        failing = [{"target": DOCTORETO_TARGET, "items": [],
                    "error": WatcherError("net down")}]
        checks = mock.Mock(side_effect=[failing, KeyboardInterrupt])

        with (
            mock.patch("builtins.input", return_value="1"),
            mock.patch.object(aw, "check_all", checks),
            mock.patch.object(aw.time, "sleep", sleeps),
        ):
            code, _ = self._run_main()

        self.assertEqual(code, 0)
        # Error was swallowed: the loop slept and ran the second check before
        # the simulated Ctrl+C.
        self.assertEqual(sleeps.call_count, 1)
        self.assertEqual(checks.call_count, 2)


class TestSetupOffline(TempFilesTest):
    def _telegram_ok(self, token: str, method: str, params: dict) -> dict:
        if method == "getMe":
            return {"ok": True, "result": {"username": "testbot"}}
        if method == "getUpdates":
            return {"ok": True, "result": [{"message": {"chat": {"id": 555}}}]}
        return {"ok": True, "result": {"message_id": 1}}

    def test_single_provider_setup_writes_v2_config(self) -> None:
        # inputs: URL, accept detected name, empty line to finish, chat prompt
        with (
            mock.patch("builtins.input", side_effect=[DOCTOR_URL, "", "", ""]),
            mock.patch.object(aw.getpass, "getpass", return_value=BOT_TOKEN),
            mock.patch.object(aw, "telegram_api", side_effect=self._telegram_ok),
            mock.patch.object(doctoreto, "fetch_html", return_value=AVAILABLE_PAGE),
        ):
            aw.setup()

        saved = json.loads(self.config_file.read_text(encoding="utf-8"))

        self.assertEqual(saved["version"], 2)
        self.assertEqual(len(saved["targets"]), 1)
        self.assertEqual(saved["targets"][0]["provider"], "doctoreto")
        self.assertEqual(saved["targets"][0]["doctor_id"], DOCTOR_ID)
        self.assertEqual(saved["targets"][0]["doctor_name"], DOCTOR_NAME)
        self.assertEqual(saved["chat_id"], "555")

        state = self.read_state()
        self.assertEqual(state["version"], 2)
        self.assertEqual(state["targets"], {})

    def test_multi_provider_setup(self) -> None:
        inputs = [DOCTOR_URL, "", P24_URL, "", "", ""]
        p24_page = P24_BOOKABLE_HTML

        with (
            mock.patch("builtins.input", side_effect=inputs),
            mock.patch.object(aw.getpass, "getpass", return_value=BOT_TOKEN),
            mock.patch.object(aw, "telegram_api", side_effect=self._telegram_ok),
            mock.patch.object(doctoreto, "fetch_html", return_value=AVAILABLE_PAGE),
            mock.patch.object(paziresh24, "fetch_html", return_value=p24_page),
        ):
            aw.setup()

        saved = json.loads(self.config_file.read_text(encoding="utf-8"))

        self.assertEqual(len(saved["targets"]), 2)
        self.assertEqual(saved["targets"][0]["provider"], "doctoreto")
        self.assertEqual(saved["targets"][1]["provider"], "paziresh24")
        self.assertEqual(saved["targets"][1]["doctor_name"], "پزشک نمونه")
        self.assertEqual(saved["targets"][1]["doctor_id"], P24_ID)

    def test_same_provider_url_replaces_previous_target(self) -> None:
        page_two = make_next_data(
            [AVAILABLE_CONSULTATION], doctor_id="dr-two"
        ).replace(DOCTOR_NAME, "دکتر دوم")

        with (
            mock.patch(
                "builtins.input",
                side_effect=[DOCTOR_URL, "", DOCTOR_URL, "", "", ""],
            ),
            mock.patch.object(aw.getpass, "getpass", return_value=BOT_TOKEN),
            mock.patch.object(aw, "telegram_api", side_effect=self._telegram_ok),
            mock.patch.object(
                doctoreto,
                "fetch_html",
                side_effect=[AVAILABLE_PAGE, page_two],
            ),
        ):
            aw.setup()

        saved = json.loads(self.config_file.read_text(encoding="utf-8"))

        self.assertEqual(len(saved["targets"]), 1)
        self.assertEqual(saved["targets"][0]["doctor_id"], DOCTOR_ID)
        self.assertEqual(saved["targets"][0]["doctor_name"], "دکتر دوم")

    def test_setup_rejects_foreign_url_with_legacy_message(self) -> None:
        with (
            mock.patch(
                "builtins.input",
                return_value="https://example.com/doctor/a/b",
            ),
            mock.patch.object(aw.getpass, "getpass", return_value=BOT_TOKEN),
            mock.patch.object(aw, "telegram_api"),
        ):
            with self.assertRaises(WatcherError) as ctx:
                aw.setup()

        self.assertIn("Please provide a doctoreto.com doctor URL", str(ctx.exception))

    def test_setup_requires_at_least_one_url(self) -> None:
        with mock.patch("builtins.input", return_value=""):
            with self.assertRaises(WatcherError) as ctx:
                aw.setup()

        self.assertIn("At least one doctor URL is required", str(ctx.exception))


class TestSaveLoadJson(TempFilesTest):
    def test_roundtrip_and_private_permissions(self) -> None:
        path = self.config_file
        payload = {"name": "دکتر تست", "token": "x"}

        aw.save_json(path, payload, private=True)

        self.assertEqual(aw.load_json(path, {}), payload)

        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_load_json_missing_file_returns_default(self) -> None:
        self.assertEqual(aw.load_json(self.state_file, {"a": 1}), {"a": 1})

    def test_no_temp_file_left_behind(self) -> None:
        aw.save_json(self.state_file, {"x": 1})

        leftovers = list(self.state_file.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])


class TestCliSmoke(unittest.TestCase):
    def test_help_runs_and_imports_everything(self) -> None:
        project_root = Path(__file__).resolve().parent.parent

        result = subprocess.run(
            [sys.executable, "appointment_watcher.py", "--help"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--setup", result.stdout)


if __name__ == "__main__":
    unittest.main()
