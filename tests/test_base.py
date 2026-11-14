"""Tests for shared provider infrastructure (providers/base.py)."""

from __future__ import annotations

import gzip
import io
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from providers import base
from providers.base import (
    MAX_DOWNLOAD_BYTES,
    ProviderSpec,
    WatcherError,
    extract_next_data,
    fetch_html,
    find_doctor_name,
    request_bytes,
    request_json,
)


class FakeResponse(io.BytesIO):
    """urlopen()-style context-manager response over canned bytes."""

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class TestRequestBytes(unittest.TestCase):
    """request_bytes: success, retry with backoff, size cap, URL encoding."""

    def test_success_returns_body(self) -> None:
        with mock.patch.object(base, "urlopen") as urlopen:
            urlopen.return_value = FakeResponse(b"payload")

            body = request_bytes("https://doctoreto.com/doctor/a/b")

        self.assertEqual(body, b"payload")
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://doctoreto.com/doctor/a/b")
        # urllib normalizes header names; the UA must be present.
        self.assertIn("User-agent", request.headers)
        self.assertEqual(request.headers["User-agent"], base.USER_AGENT)

    def test_non_ascii_url_is_percent_encoded(self) -> None:
        # Regression: Persian slugs in doctor URLs must be encoded before
        # urllib sees them, otherwise the request raises UnicodeEncodeError.
        with mock.patch.object(base, "urlopen") as urlopen:
            urlopen.return_value = FakeResponse(b"payload")

            request_bytes("https://www.paziresh24.com/dr/دکتر-هدیه-جباری-0")

        request = urlopen.call_args[0][0]
        self.assertEqual(
            request.full_url,
            "https://www.paziresh24.com/dr/"
            "%D8%AF%DA%A9%D8%AA%D8%B1-%D9%87%D8%AF%DB%8C%D9%87-%D8%AC%D8%A8%D8%A7%D8%B1%DB%8C-0",
        )

    def test_encode_url_leaves_ascii_untouched(self) -> None:
        url = "https://doctoreto.com/doctor/tehran/doctor-sample-456?a=1#top"

        self.assertEqual(base.encode_url(url), url)

    def test_retries_then_succeeds(self) -> None:
        with (
            mock.patch.object(base, "urlopen") as urlopen,
            mock.patch.object(base.time, "sleep") as sleep,
        ):
            urlopen.side_effect = [
                HTTPError("url", 500, "boom", None, None),  # type: ignore[arg-type]
                FakeResponse(b"ok"),
            ]

            body = request_bytes("https://doctoreto.com/doctor/a/b")

        self.assertEqual(body, b"ok")
        sleep.assert_called_once()

    def test_exhausted_retries_raise_watcher_error(self) -> None:
        with (
            mock.patch.object(base, "urlopen") as urlopen,
            mock.patch.object(base.time, "sleep") as sleep,
        ):
            urlopen.side_effect = URLError("down")

            with self.assertRaises(WatcherError) as ctx:
                request_bytes("https://doctoreto.com/doctor/a/b")

        self.assertIn("Connection failed", str(ctx.exception))
        self.assertEqual(sleep.call_count, 2)  # backoff between the 3 attempts

    def test_oversized_response_raises(self) -> None:
        # Legacy quirk, pinned: an oversized body fails every attempt and is
        # ultimately reported as a connection failure.
        with (
            mock.patch.object(base, "urlopen") as urlopen,
            mock.patch.object(base.time, "sleep") as sleep,
        ):
            urlopen.side_effect = lambda *a, **k: FakeResponse(
                b"x" * (MAX_DOWNLOAD_BYTES + 1)
            )

            with self.assertRaises(WatcherError) as ctx:
                request_bytes("https://doctoreto.com/doctor/a/b")

        self.assertIn("Connection failed", str(ctx.exception))
        self.assertIn("too large", str(ctx.exception))


class TestRequestJson(unittest.TestCase):
    def test_returns_dict(self) -> None:
        with mock.patch.object(base, "request_bytes", return_value=b'{"ok": true}'):
            self.assertEqual(request_json("https://x"), {"ok": True})

    def test_invalid_json_raises(self) -> None:
        with mock.patch.object(base, "request_bytes", return_value=b"<html>"):
            with self.assertRaises(WatcherError) as ctx:
                request_json("https://x")

        self.assertIn("invalid JSON", str(ctx.exception))

    def test_non_dict_json_raises(self) -> None:
        with mock.patch.object(base, "request_bytes", return_value=b"[1, 2]"):
            with self.assertRaises(WatcherError) as ctx:
                request_json("https://x")

        self.assertIn("unexpected JSON structure", str(ctx.exception))


class TestFetchHtml(unittest.TestCase):
    def test_plain_html(self) -> None:
        with mock.patch.object(base, "request_bytes", return_value="<html></html>".encode()):
            self.assertEqual(fetch_html("https://x"), "<html></html>")

    def test_gzip_response_is_decompressed(self) -> None:
        gzipped = gzip.compress("<html>سلام</html>".encode("utf-8"))

        with mock.patch.object(base, "request_bytes", return_value=gzipped):
            self.assertEqual(fetch_html("https://x"), "<html>سلام</html>")

    def test_corrupt_gzip_raises(self) -> None:
        with mock.patch.object(base, "request_bytes", return_value=b"\x1f\x8bbroken"):
            with self.assertRaises(WatcherError) as ctx:
                fetch_html("https://x")

        self.assertIn("compressed website response is invalid", str(ctx.exception))

    def test_non_utf8_raises(self) -> None:
        with mock.patch.object(base, "request_bytes", return_value=b"\xff\xfe\xfa"):
            with self.assertRaises(WatcherError) as ctx:
                fetch_html("https://x")

        self.assertIn("not UTF-8", str(ctx.exception))


class TestExtractNextData(unittest.TestCase):
    @staticmethod
    def _page(payload: str) -> str:
        return (
            '<html><head>'
            f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'
            "</head></html>"
        )

    def test_extracts_json(self) -> None:
        html = self._page('{"props": {"pageProps": {}}}')

        self.assertEqual(extract_next_data(html), {"props": {"pageProps": {}}})

    def test_missing_script_raises(self) -> None:
        with self.assertRaises(WatcherError) as ctx:
            extract_next_data("<html><body>no data</body></html>")

        self.assertIn("Could not find __NEXT_DATA__", str(ctx.exception))

    def test_malformed_json_raises(self) -> None:
        with self.assertRaises(WatcherError) as ctx:
            extract_next_data(self._page("{not json"))

        self.assertIn("Could not parse __NEXT_DATA__", str(ctx.exception))

    def test_non_dict_payload_raises(self) -> None:
        with self.assertRaises(WatcherError) as ctx:
            extract_next_data(self._page("42"))

        self.assertIn("unexpected structure", str(ctx.exception))


class TestFindDoctorName(unittest.TestCase):
    def test_finds_fullname_deep_in_tree(self) -> None:
        data = {"props": {"pageProps": {"doctor": {"fullName": "دکتر سارا احمدی"}}}}

        self.assertEqual(find_doctor_name(data), "دکتر سارا احمدی")

    def test_finds_full_name_key(self) -> None:
        data = {"data": {"items": [{"full_name": "Ali Rezaei"}]}}

        self.assertEqual(find_doctor_name(data), "Ali Rezaei")

    def test_returns_none_when_absent(self) -> None:
        self.assertIsNone(find_doctor_name({"props": {"other": 1}}))

    def test_returns_none_for_too_short_values(self) -> None:
        self.assertIsNone(find_doctor_name({"fullName": "ab"}))

    def test_prefers_doctor_container_over_generic_walk(self) -> None:
        data = {
            "doctor": {"fullName": "دکتر درست"},
            "sidebar": {"fullName": "دکتر نادرست"},
        }

        self.assertEqual(find_doctor_name(data), "دکتر درست")


class TestProviderSpecContract(unittest.TestCase):
    def test_fields(self) -> None:
        spec = ProviderSpec(
            name="x",
            hosts=("x.com",),
            normalize_url=lambda url: url,
            extract_doctor_id=lambda url: "id",
            detect_name=lambda url: None,
            fetch_open_appointments=lambda url, doctor_id: [],
        )

        self.assertEqual(spec.name, "x")
        self.assertEqual(spec.normalize_url("u"), "u")
        self.assertEqual(spec.extract_doctor_id("u"), "id")
        self.assertIsNone(spec.detect_name("u"))
        self.assertEqual(spec.fetch_open_appointments("u", "id"), [])


if __name__ == "__main__":
    unittest.main()
