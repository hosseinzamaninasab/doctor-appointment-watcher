#!/usr/bin/env python3
"""Doctoreto Appointment Watcher.

A Termux-friendly watcher that:
- accepts a Doctoreto doctor URL during setup,
- extracts the doctor ID automatically,
- tries to detect the doctor's Persian name from the page,
- accepts a Persian name manually if auto-detection fails,
- configures Telegram automatically after the user presses Start,
- checks appointments periodically and notifies Telegram on changes.

Standard library only.
"""

from __future__ import annotations

import argparse
import getpass
import gzip
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


CHECK_INTERVAL_SECONDS = 10 * 60
REQUEST_TIMEOUT_SECONDS = 45
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024

APP_DIR = Path.home() / ".config" / "doctoreto-watcher"
CONFIG_FILE = APP_DIR / "config.json"
STATE_FILE = APP_DIR / "state.json"

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; Mobile) "
    "AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36"
)


class WatcherError(RuntimeError):
    """A recoverable watcher error."""


def log(message: str) -> None:
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    print(f"[{now}] {message}", flush=True)


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

    for attempt in range(1, attempts + 1):
        try:
            request = Request(url, data=data, headers=request_headers)
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read(MAX_DOWNLOAD_BYTES + 1)

            if len(body) > MAX_DOWNLOAD_BYTES:
                raise WatcherError("Website response is too large.")

            return body

        except (HTTPError, URLError, TimeoutError, OSError, WatcherError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2**attempt)

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


def telegram_api(token: str, method: str, params: dict[str, str]) -> dict[str, Any]:
    payload = urlencode(params).encode("utf-8")

    result = request_json(
        f"https://api.telegram.org/bot{token}/{method}",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if result.get("ok") is not True:
        raise WatcherError(
            f"Telegram API error: {result.get('description', 'Unknown error')}"
        )

    return result


def send_telegram(token: str, chat_id: str, text: str) -> None:
    telegram_api(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        },
    )


def save_json(path: Path, value: dict[str, Any], *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if private:
        os.chmod(temp_path, 0o600)

    os.replace(temp_path, path)

    if private:
        os.chmod(path, 0o600)


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default.copy()
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default.copy()


def normalize_doctor_url(url: str) -> str:
    url = url.strip().split("#", 1)[0].split("?", 1)[0].rstrip("/")

    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise WatcherError("Doctor URL must start with http:// or https://.")

    if parsed.netloc not in {"doctoreto.com", "www.doctoreto.com"}:
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


def fetch_doctor_page(doctor_url: str) -> str:
    raw = request_bytes(
        doctor_url,
        headers={"Accept": "text/html,application/xhtml+xml"},
    )

    # Doctoreto may send gzip even when the client did not explicitly request it.
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


def detect_doctor_name(doctor_url: str) -> str | None:
    html_text = fetch_doctor_page(doctor_url)
    next_data = extract_next_data(html_text)
    return find_doctor_name(next_data)


def load_config() -> dict[str, str]:
    config = load_json(CONFIG_FILE, {})

    required_keys = ("doctor_name", "doctor_id", "doctor_url", "bot_token", "chat_id")
    result = {key: str(config.get(key, "")).strip() for key in required_keys}

    missing = [key for key, value in result.items() if not value]
    if missing:
        raise WatcherError(
            f"Configuration is incomplete. Run: {Path(sys.argv[0]).name} --setup"
        )

    return result


def choose_telegram_chat(token: str) -> str:
    print("\nOpen your bot in Telegram and press Start (or send /start).")
    input("When finished, press Enter here... ")

    updates = telegram_api(token, "getUpdates", {"limit": "100"}).get("result", [])

    chats: list[dict[str, Any]] = []
    seen: set[str] = set()

    if isinstance(updates, list):
        for update in reversed(updates):
            if not isinstance(update, dict):
                continue

            message = update.get("message") or update.get("edited_message")
            chat = message.get("chat") if isinstance(message, dict) else None

            if not isinstance(chat, dict) or "id" not in chat:
                continue

            chat_id = str(chat["id"])

            if chat_id not in seen:
                seen.add(chat_id)
                chats.append(chat)

    if not chats:
        raise WatcherError(
            "No Telegram chat was found. Press Start in your bot and run setup again."
        )

    chosen = chats[0]

    if len(chats) > 1:
        print("\nMultiple chats were found:")
        for index, chat in enumerate(chats[:10], start=1):
            title = (
                chat.get("title")
                or chat.get("username")
                or " ".join(
                    str(chat.get(key, ""))
                    for key in ("first_name", "last_name")
                ).strip()
                or "Unnamed chat"
            )
            print(f"  {index}) {title}")

        answer = input("Choose chat [1]: ").strip() or "1"

        try:
            chosen = chats[int(answer) - 1]
        except (ValueError, IndexError) as exc:
            raise WatcherError("Invalid chat selection.") from exc

    return str(chosen["id"])


def setup() -> None:
    print("\n=== Doctoreto Appointment Watcher Setup ===\n")

    doctor_url = normalize_doctor_url(input("Doctor URL: "))
    doctor_id = extract_doctor_id(doctor_url)

    print("\nChecking the doctor page and trying to detect the doctor's name...")
    detected_name = None

    try:
        detected_name = detect_doctor_name(doctor_url)
    except WatcherError as exc:
        print(f"Automatic name detection failed: {exc}")

    if detected_name:
        print(f"Detected doctor name: {detected_name}")
        answer = input("Press Enter to accept, or type another name: ").strip()
        doctor_name = answer or detected_name
    else:
        doctor_name = input(
            "Doctor name (Persian and Unicode are supported): "
        ).strip()

        if not doctor_name:
            raise WatcherError("Doctor name cannot be empty.")

    print(f"\nDoctor ID extracted automatically: {doctor_id}")

    token = getpass.getpass("\nTelegram Bot Token: ").strip()

    if not token:
        raise WatcherError("Telegram bot token cannot be empty.")

    me = telegram_api(token, "getMe", {})
    bot = me.get("result", {})

    username = (
        str(bot.get("username", "")).strip()
        if isinstance(bot, dict)
        else ""
    )

    if username:
        print(f"Telegram bot verified: @{username}")
    else:
        print("Telegram bot token verified.")

    chat_id = choose_telegram_chat(token)

    config = {
        "doctor_name": doctor_name,
        "doctor_id": doctor_id,
        "doctor_url": doctor_url,
        "bot_token": token,
        "chat_id": chat_id,
        "check_interval_seconds": CHECK_INTERVAL_SECONDS,
    }

    save_json(CONFIG_FILE, config, private=True)

    # Reset old availability state when the watched doctor changes.
    save_json(
        STATE_FILE,
        {
            "last_alert_signature": "",
            "consecutive_errors": 0,
            "error_alerted": False,
        },
    )

    send_telegram(
        token,
        chat_id,
        f"✅ Appointment watcher is configured.\n\nDoctor: {doctor_name}",
    )

    print("\nSetup completed successfully.")
    print(f"Configuration saved to: {CONFIG_FILE}")
    print(f"Doctor: {doctor_name}")
    print("A test message was sent to Telegram.")


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


def availability_signature(items: list[dict[str, str]]) -> str:
    return json.dumps(
        items,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_alert(config: dict[str, str], items: list[dict[str, str]]) -> str:
    lines = [f"🚨 نوبت {config['doctor_name']} باز شد!", ""]

    for item in items:
        when = " | ".join(
            part for part in (item["date"], item["time"]) if part
        )

        lines.append(f"مطب: {item['place']}")

        if when:
            lines.append(f"اولین زمان آزاد: {when}")

        lines.append("")

    lines.extend(
        (
            "برای رزرو سریع وارد سایت شوید:",
            config["doctor_url"],
        )
    )

    return "\n".join(lines)


def default_state() -> dict[str, Any]:
    return {
        "last_alert_signature": "",
        "consecutive_errors": 0,
        "error_alerted": False,
    }


def check_once(config: dict[str, str]) -> bool:
    state = load_json(STATE_FILE, default_state())

    html_text = fetch_doctor_page(config["doctor_url"])
    items = extract_open_appointments(html_text, config["doctor_id"])
    signature = availability_signature(items)

    if items:
        if signature != state.get("last_alert_signature"):
            send_telegram(
                config["bot_token"],
                config["chat_id"],
                build_alert(config, items),
            )
            state["last_alert_signature"] = signature
            log(
                f"Appointment available; Telegram notification sent "
                f"({len(items)} location(s))."
            )
        else:
            log("Appointment is still available; notification was already sent.")
    else:
        state["last_alert_signature"] = ""
        log("No appointment is currently available.")

    state["consecutive_errors"] = 0
    state["error_alerted"] = False
    state["last_success_at"] = (
        datetime.now().astimezone().isoformat(timespec="seconds")
    )

    save_json(STATE_FILE, state)

    return bool(items)


def record_error(config: dict[str, str], error: Exception) -> None:
    state = load_json(STATE_FILE, default_state())

    count = int(state.get("consecutive_errors", 0)) + 1
    state["consecutive_errors"] = count

    log(f"Error ({count} consecutive): {error}")

    # One warning after roughly one hour of continuous failures.
    if count >= 6 and not state.get("error_alerted"):
        try:
            send_telegram(
                config["bot_token"],
                config["chat_id"],
                (
                    f"⚠️ پایش نوبت {config['doctor_name']} "
                    "چند بار پیاپی با خطا مواجه شده است. "
                    "لطفاً اینترنت یا وضعیت برنامه را بررسی کنید."
                ),
            )
            state["error_alerted"] = True

        except Exception as telegram_error:
            log(f"Failed to send Telegram error notification: {telegram_error}")

    save_json(STATE_FILE, state)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Watch a Doctoreto doctor and notify Telegram."
    )

    parser.add_argument(
        "--setup",
        action="store_true",
        help="Configure doctor and Telegram settings.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Send a Telegram test message.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check once and exit.",
    )

    args = parser.parse_args()

    try:
        if args.setup:
            setup()
            return 0

        config = load_config()

        if args.test:
            send_telegram(
                config["bot_token"],
                config["chat_id"],
                (
                    "✅ Test message received.\n\n"
                    f"Doctor: {config['doctor_name']}"
                ),
            )
            print("Test message sent.")
            return 0

        interval = int(
            load_json(CONFIG_FILE, {}).get(
                "check_interval_seconds",
                CHECK_INTERVAL_SECONDS,
            )
        )

        log(
            f"Watching {config['doctor_name']} "
            f"every {interval // 60} minute(s)."
        )

        while True:
            try:
                check_once(config)
            except Exception as exc:
                record_error(config, exc)

            if args.once:
                return 0

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nWatcher stopped.")
        return 0

    except WatcherError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
