#!/usr/bin/env python3
"""Doctor Appointment Watcher.

A Termux-friendly watcher that:
- accepts doctor URLs from supported providers (Doctoreto, Paziresh24),
- extracts doctor IDs automatically,
- tries to detect the doctor's Persian name from the page,
- configures Telegram automatically after the user presses Start,
- checks all configured providers periodically, compares availability in the
  terminal and notifies Telegram per provider when appointments appear.

Booking always happens on the official provider website; this tool never
books automatically and never sends credentials to any provider.

Standard library only.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from providers import DEFAULT_PROVIDER, REGISTRY, get_provider, provider_for_url
from providers.base import (
    WatcherError,
    WatcherHTTPError,
    request_json,
)
from providers.model import (
    Appointment,
    describe_item,
    normalize_items,
    signature_for_items,
)


CHECK_INTERVAL_SECONDS = 10 * 60
MIN_INTERVAL_SECONDS = 60
ERROR_ALERT_THRESHOLD = 6  # consecutive failures before a Telegram warning

CONFIG_VERSION = 2
STATE_VERSION = 2

APP_DIR = Path.home() / ".config" / "doctoreto-watcher"
CONFIG_FILE = APP_DIR / "config.json"
STATE_FILE = APP_DIR / "state.json"

REQUIRED_TARGET_KEYS = ("provider", "doctor_name", "doctor_id", "doctor_url")


def log(message: str) -> None:
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    print(f"[{now}] {message}", flush=True)


# ---------------------------------------------------------------------------
# HTTP / Telegram
# ---------------------------------------------------------------------------

def _telegram_retry_after(body: bytes | None) -> int | None:
    """Extract retry_after (seconds) from a Telegram 429 response body."""
    if not body:
        return None

    try:
        data = json.loads(body.decode("utf-8"))
        seconds = int(data.get("parameters", {}).get("retry_after") or 0)
    except (AttributeError, TypeError, ValueError, UnicodeDecodeError,
            json.JSONDecodeError):
        return None

    return seconds if seconds > 0 else None


def telegram_api(token: str, method: str, params: dict[str, str]) -> dict[str, Any]:
    payload = urlencode(params).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/{method}"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    result: dict[str, Any] | None = None

    for attempt in (1, 2):
        try:
            result = request_json(url, data=payload, headers=headers)
            break
        except WatcherHTTPError as exc:
            # Respect Telegram's 429 retry_after hint once; anything else
            # (or a repeat 429) propagates to the caller.
            retry_after = _telegram_retry_after(exc.body)

            if not (
                attempt == 1
                and exc.status == 429
                and retry_after is not None
                and retry_after <= 120
            ):
                raise

            log(f"Telegram rate limit hit; retrying in {retry_after}s.")
            time.sleep(retry_after)

    if result is None:  # pragma: no cover - defensive
        raise WatcherError("Telegram request did not return a result.")

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


# ---------------------------------------------------------------------------
# Local persistence
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Configuration (v2) and migration from legacy flat configs
# ---------------------------------------------------------------------------

def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _parse_interval(raw: Any) -> int:
    try:
        interval = int(raw)
    except (TypeError, ValueError):
        return CHECK_INTERVAL_SECONDS

    return interval if interval >= MIN_INTERVAL_SECONDS else CHECK_INTERVAL_SECONDS


def migrate_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw config dict to the v2 shape.

    Legacy flat configs (one doctor, no "targets" key) are converted into a
    one-entry target list, with the provider inferred from the URL host so
    existing setups keep working unchanged.
    """
    interval = _parse_interval(raw.get("check_interval_seconds"))

    targets_raw = raw.get("targets")
    targets: list[dict[str, str]] = []

    if isinstance(targets_raw, list):
        for entry in targets_raw:
            if not isinstance(entry, dict):
                continue
            targets.append(
                {key: _clean_str(entry.get(key)) for key in REQUIRED_TARGET_KEYS}
            )
    else:
        legacy = {key: _clean_str(raw.get(key)) for key in REQUIRED_TARGET_KEYS}
        # Legacy configs have no "provider" field; completeness is judged on
        # the doctor fields only (provider is inferred from the URL below).
        complete = all(legacy[key] for key in REQUIRED_TARGET_KEYS if key != "provider")
        targets = [legacy] if complete else []

    for target in targets:
        if not target["provider"]:
            provider = provider_for_url(target["doctor_url"])
            target["provider"] = provider.name if provider else DEFAULT_PROVIDER

    return {
        "bot_token": _clean_str(raw.get("bot_token")),
        "chat_id": _clean_str(raw.get("chat_id")),
        "check_interval_seconds": interval,
        "targets": targets,
    }


def load_config() -> dict[str, Any]:
    config = migrate_config(load_json(CONFIG_FILE, {}))

    if not config["targets"]:
        raise WatcherError(
            f"Configuration is incomplete. Run: {Path(sys.argv[0]).name} --setup"
        )

    if not config["bot_token"] or not config["chat_id"]:
        raise WatcherError(
            f"Configuration is incomplete. Run: {Path(sys.argv[0]).name} --setup"
        )

    for index, target in enumerate(config["targets"], start=1):
        for key in REQUIRED_TARGET_KEYS:
            if not target[key]:
                raise WatcherError(
                    "Configuration is incomplete "
                    f"(target {index} is missing {key}). "
                    f"Run: {Path(sys.argv[0]).name} --setup"
                )

        # Unknown provider names must fail loudly, not silently.
        get_provider(target["provider"])

    return config


def config_exists() -> bool:
    config = migrate_config(load_json(CONFIG_FILE, {}))

    if not config["bot_token"] or not config["chat_id"]:
        return False

    for target in config["targets"]:
        if not all(target[key] for key in REQUIRED_TARGET_KEYS):
            return False

        try:
            get_provider(target["provider"])
        except WatcherError:
            return False

    return True


# ---------------------------------------------------------------------------
# Per-target state (v2): one independent fingerprint per provider + doctor
# ---------------------------------------------------------------------------

def target_key(target: dict[str, str]) -> str:
    return f"{target['provider']}:{target['doctor_id']}"


def default_target_state() -> dict[str, Any]:
    return {
        "last_alert_signature": "",
        "consecutive_errors": 0,
        "error_alerted": False,
        "last_success_at": None,
    }


def load_state(config: dict[str, Any]) -> dict[str, Any]:
    raw = load_json(STATE_FILE, {})

    if raw.get("version") == STATE_VERSION and isinstance(raw.get("targets"), dict):
        return raw

    # Legacy flat state: re-attach the fingerprint to the first configured
    # target so an upgrade does not cause a duplicate notification.
    state: dict[str, Any] = {"version": STATE_VERSION, "targets": {}}

    if config["targets"]:
        try:
            error_count = int(raw.get("consecutive_errors") or 0)
        except (TypeError, ValueError):
            error_count = 0

        state["targets"][target_key(config["targets"][0])] = {
            "last_alert_signature": str(raw.get("last_alert_signature", "")),
            "consecutive_errors": error_count,
            "error_alerted": bool(raw.get("error_alerted", False)),
            "last_success_at": raw.get("last_success_at"),
        }

    return state


def save_state(state: dict[str, Any]) -> None:
    save_json(STATE_FILE, state)


def get_target_state(state: dict[str, Any], key: str) -> dict[str, Any]:
    targets = state.setdefault("targets", {})

    entry = targets.get(key)

    if not isinstance(entry, dict):
        entry = default_target_state()
        targets[key] = entry

    return entry


def reset_state() -> None:
    try:
        STATE_FILE.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Watch logic
# ---------------------------------------------------------------------------

def check_target(
    config: dict[str, Any],
    target: dict[str, str],
    state: dict[str, Any],
) -> list[Appointment]:
    """Check one provider target, updating its independent state entry.

    Raises on failure; state is mutated only after a successful check.
    """
    provider = get_provider(target["provider"])
    target_state = get_target_state(state, target_key(target))

    items = provider.fetch_open_appointments(
        target["doctor_url"], target["doctor_id"]
    )

    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
    appointments = normalize_items(
        provider.name,
        target["doctor_id"],
        target["doctor_name"],
        target["doctor_url"],
        items,
        checked_at,
    )

    signature = signature_for_items(appointments)

    if appointments:
        if signature != target_state.get("last_alert_signature"):
            send_telegram(
                config["bot_token"],
                config["chat_id"],
                build_alert(target, appointments),
            )
            target_state["last_alert_signature"] = signature
            log(
                f"[{provider.name}] Appointment available; Telegram "
                f"notification sent ({len(appointments)} slot(s))."
            )
        else:
            log(
                f"[{provider.name}] Appointment is still available; "
                "notification was already sent."
            )
    else:
        target_state["last_alert_signature"] = ""
        log(f"[{provider.name}] No appointment is currently available.")

    target_state["consecutive_errors"] = 0
    target_state["error_alerted"] = False
    target_state["last_success_at"] = checked_at

    return appointments


def record_target_error(
    config: dict[str, Any],
    target: dict[str, str],
    error: Exception,
    state: dict[str, Any],
) -> None:
    target_state = get_target_state(state, target_key(target))

    try:
        count = int(target_state.get("consecutive_errors") or 0) + 1
    except (TypeError, ValueError):
        count = 1

    target_state["consecutive_errors"] = count

    log(f"[{target['provider']}] Error ({count} consecutive): {error}")

    # One warning after roughly one hour of continuous failures.
    if count >= ERROR_ALERT_THRESHOLD and not target_state.get("error_alerted"):
        try:
            send_telegram(
                config["bot_token"],
                config["chat_id"],
                (
                    f"⚠️ پایش نوبت {target['doctor_name']} "
                    f"({target['provider']}) "
                    "چند بار پیاپی با خطا مواجه شده است. "
                    "لطفاً اینترنت یا وضعیت برنامه را بررسی کنید."
                ),
            )
            target_state["error_alerted"] = True

        except Exception as telegram_error:
            log(f"Failed to send Telegram error notification: {telegram_error}")


def check_all(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Check every configured target independently.

    A failure on one provider never discards results from another provider.
    """
    state = load_state(config)
    results: list[dict[str, Any]] = []

    for target in config["targets"]:
        entry: dict[str, Any] = {
            "target": target,
            "items": [],
            "error": None,
        }

        try:
            entry["items"] = check_target(config, target, state)
        except Exception as exc:
            entry["error"] = exc
            record_target_error(config, target, exc, state)

        results.append(entry)

    save_state(state)

    return results


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

def build_alert(target: dict[str, str], items: list[Appointment]) -> str:
    lines = [
        f"🚨 نوبت {target['doctor_name']} ({target['provider']}) باز شد!",
        "",
    ]

    for item in items:
        lines.append(f"مطب: {item.clinic}")

        when = " | ".join(part for part in (item.date, item.time) if part)

        if when:
            lines.append(f"اولین زمان آزاد: {when}")

        lines.append("")

    lines.extend(
        (
            "برای رزرو سریع وارد سایت شوید:",
            target["doctor_url"],
        )
    )

    return "\n".join(lines)


def comparison_lines(
    results: list[dict[str, Any]],
    now_text: str,
) -> list[str]:
    lines = [f"=== Appointment comparison ({now_text}) ===", ""]

    for entry in results:
        target = entry["target"]
        lines.append(f"{target['provider']} — {target['doctor_name']}")

        if entry["error"] is not None:
            lines.append(f"  ⚠️ Check failed: {entry['error']}")
        elif entry["items"]:
            lines.append(f"  ✅ {len(entry['items'])} open slot(s):")
            for item in entry["items"]:
                lines.append(f"  • {describe_item(item)}")
            lines.append(f"  Book on the official site: {target['doctor_url']}")
        else:
            lines.append("  ❌ No open appointments")

        lines.append("")

    return lines


def print_comparison(results: list[dict[str, Any]]) -> None:
    now_text = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    print("\n".join(comparison_lines(results, now_text)))


# ---------------------------------------------------------------------------
# Interactive setup
# ---------------------------------------------------------------------------

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


def configure_target(raw_url: str) -> dict[str, str]:
    """Build one watch target from a doctor URL typed by the user."""
    provider = provider_for_url(raw_url)

    if provider is None:
        # No provider matched; let the default provider's URL validation
        # produce the familiar guidance for unsupported or malformed URLs.
        provider = get_provider(DEFAULT_PROVIDER)

    doctor_url = provider.normalize_url(raw_url)
    doctor_id = provider.extract_doctor_id(doctor_url)

    print("\nChecking the doctor page and trying to detect the doctor's name...")
    detected_name = None

    try:
        detected_name = provider.detect_name(doctor_url)
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

    print(f"Doctor ID extracted automatically: {doctor_id}")

    return {
        "provider": provider.name,
        "doctor_name": doctor_name,
        "doctor_id": doctor_id,
        "doctor_url": doctor_url,
    }


def collect_targets() -> list[dict[str, str]]:
    """Ask for one doctor URL per provider until the user submits an empty line."""
    print(f"Supported providers: {', '.join(sorted(REGISTRY))}")
    print("Enter a doctor URL for each provider you want to watch.")

    targets: list[dict[str, str]] = []

    while True:
        suffix = " (empty line to finish)" if targets else ""
        raw_url = input(f"\nDoctor URL{suffix}: ").strip()

        if not raw_url:
            if not targets:
                raise WatcherError("At least one doctor URL is required.")

            return targets

        target = configure_target(raw_url)

        replaced = [t for t in targets if t["provider"] == target["provider"]]
        targets = [t for t in targets if t["provider"] != target["provider"]]
        targets.append(target)

        if replaced:
            print(
                f"Replaced the previous {target['provider']} doctor "
                f"({replaced[0]['doctor_name']})."
            )

        print(
            f"Added: {target['doctor_name']} ({target['provider']}). "
            f"{len(targets)} provider(s) configured."
        )


def setup() -> None:
    print("\n=== Doctor Appointment Watcher Setup ===\n")

    targets = collect_targets()

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
        "version": CONFIG_VERSION,
        "check_interval_seconds": CHECK_INTERVAL_SECONDS,
        "bot_token": token,
        "chat_id": chat_id,
        "targets": targets,
    }

    save_json(CONFIG_FILE, config, private=True)

    # Fresh state: the watched doctors changed.
    save_json(
        STATE_FILE,
        {"version": STATE_VERSION, "targets": {}},
    )

    doctor_list = "\n".join(
        f"- {t['doctor_name']} ({t['provider']})" for t in targets
    )
    send_telegram(
        token,
        chat_id,
        f"✅ Appointment watcher is configured.\n\n{doctor_list}",
    )

    print("\nSetup completed successfully.")
    print(f"Configuration saved to: {CONFIG_FILE}")
    print(f"Watching {len(targets)} doctor(s).")
    print("A test message was sent to Telegram.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def show_main_menu() -> str:
    print("\n=== Doctor Appointment Watcher ===\n")
    print("1) Start watcher")
    print("2) Configure doctors")
    print("3) Exit")

    choice = input("\nChoose an option [1]: ").strip() or "1"

    if choice not in {"1", "2", "3"}:
        raise WatcherError("Invalid menu selection.")

    return choice


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Watch supported doctor appointment pages, compare "
                    "availability and notify Telegram."
    )

    parser.add_argument(
        "--setup",
        action="store_true",
        help="Configure doctors and Telegram settings.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Send a Telegram test message.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check all providers once, print the comparison and exit.",
    )

    args = parser.parse_args()

    try:
        if args.setup:
            reset_state()
            setup()
            return 0

        if args.test:
            config = load_config()

            send_telegram(
                config["bot_token"],
                config["chat_id"],
                (
                    "Test message received.\n\n"
                    f"Watching: "
                    + ", ".join(
                        f"{t['doctor_name']} ({t['provider']})"
                        for t in config["targets"]
                    )
                ),
            )
            print("Test message sent.")
            return 0

        if args.once:
            config = load_config()
            results = check_all(config)
            print_comparison(results)
            return 0

        # First run: automatically start initial setup.
        if not config_exists():
            print("\nNo configuration found. Starting initial setup...")
            setup()
            return 0

        # Later runs: configuration already exists, so show the menu.
        choice = show_main_menu()

        if choice == "2":
            reset_state()
            setup()
            return 0

        if choice == "3":
            print("Goodbye.")
            return 0

        config = load_config()
        interval = int(config["check_interval_seconds"])

        doctors = ", ".join(
            f"{t['doctor_name']} ({t['provider']})" for t in config["targets"]
        )
        log(f"Watching {doctors} every {interval // 60} minute(s).")

        while True:
            results = check_all(config)
            print_comparison(results)

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nWatcher stopped.")
        return 0

    except WatcherError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
