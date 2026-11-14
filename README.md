<p align="center">
  <a href="./README.md">🇬🇧 English</a>
  &nbsp;•&nbsp;
  <a href="./README.fa.md">🇮🇷 فارسی</a>
</p>

# **Doctor Appointment Watcher**

A lightweight Python tool that monitors doctor appointment pages on supported platforms, compares availability side by side in the terminal, and sends a Telegram notification when a new available appointment is detected.

Supported platforms: **Doctoreto** (`doctoreto.com`) and **Paziresh24** (`paziresh24.com`). More platforms can be added through community contributions.

## **Features**

* Monitor one doctor per supported platform at the same time
* Side-by-side availability comparison in the terminal
* Independent per-provider and per-doctor monitoring state
* Telegram notifications with official booking links
* No duplicate notifications for the same availability
* A failure on one platform never hides results from the other
* Persist configuration locally; upgrade from old single-doctor configs automatically
* Reconfigure doctors at any time
* Configurable checking interval
* Persian/Unicode doctor names and URLs are supported
* Termux-friendly
* Uses only the Python standard library
* No automatic booking — you always book on the official website

## **Supported Platforms**

|  **Platform**  |  **Status**  |  **Data source**  |
|---|---|---|
|  Doctoreto (`doctoreto.com`)  |  ✅ Supported  |  Public doctor page (`__NEXT_DATA__`)  |
|  Paziresh24 (`paziresh24.com`)  |  ✅ Supported  |  Public doctor page (`__NEXT_DATA__` / `samanDoctorAvailabilityStatus`)  |
|  Other platforms  |  🤝 Community contributions welcome  |  —  |

## **Requirements**

* Termux on Android (or any machine with Python ≥ 3.10)
* Internet connection
* A doctor's page on a supported platform
* A Telegram bot

Python does not need to be installed manually on Termux. The setup script checks for Python and installs it automatically when necessary.

## **Installation**

Clone the repository:

```
git clone https://github.com/hosseinzamaninasab/doctor-appointment-watcher.git
cd doctor-appointment-watcher
```

Run the setup script:

```
chmod +x setup.sh
./setup.sh
```

The setup script prepares the Termux environment and starts the application. On a regular Linux/macOS machine you can skip it and run the watcher directly:

```
python3 appointment_watcher.py
```

## **First Run**

On the first run, no previous configuration exists, so the application automatically starts the initial setup.

You will be asked for:

1. A doctor URL for each platform you want to watch (press Enter on an empty line to finish)
2. Doctor names (auto-detected from the page; Persian is supported)
3. Telegram bot token
4. Telegram chat

Example:

```
=== Doctor Appointment Watcher Setup ===

Supported providers: doctoreto, paziresh24

Doctor URL:
> https://doctoreto.com/doctor/tehran/...

Detected doctor name: نام و نام خانوادگی دکتر
Doctor ID extracted automatically: ...

Doctor URL (empty line to finish):
> https://paziresh24.com/dr/دکتر-...

Doctor URL (empty line to finish):
>

Telegram Bot Token:
> ********

Open your bot in Telegram and press Start.

Setup completed successfully.
```

You can watch a single platform or both — one doctor per platform, up to one target per provider. Providing a second URL for the same provider replaces the previous one.

The configuration is stored locally so you do not need to enter it every time. Existing single-doctor configurations from older versions are migrated automatically; nothing is lost.

## **Running the Watcher**

After the initial setup, run:

```
./setup.sh
```

or:

```
python appointment_watcher.py
```

When an existing configuration is found, the application shows:

```
=== Doctor Appointment Watcher ===

1) Start watcher
2) Configure doctors
3) Exit
```

### **Start Watcher**

Select `1`. Every configured provider is checked each cycle and a comparison is printed:

```
=== Appointment comparison (2026-11-13 11:19) ===

doctoreto — دکتر علی رضایی
  ❌ No open appointments

paziresh24 — پزشک نمونه
  ✅ 2 open slot(s):
  • مطب نمونه | 2026-12-01 10:30
  • ویزیت آنلاین پذیرش24 | 2026-11-14 15:00
  Book on the official site: https://paziresh24.com/dr/...
```

If one provider fails (network error, page structure change), the other provider's results are still shown and monitored:

```
doctoreto — دکتر علی رضایی
  ⚠️ Check failed: Connection failed: HTTP Error 404: Not Found
```

### **One-Shot Check**

```
python appointment_watcher.py --once
```

Checks every configured provider once, prints the comparison and exits. Useful for testing or cron-based setups.

### **Telegram Test Message**

```
python appointment_watcher.py --test
```

### **Configure Doctors**

Select `2` to run setup again. The previous configuration is replaced and monitoring state is reset.

### **Exit**

Select `3`.

## **Stop the Watcher**

Press `Ctrl + C`.

On Termux, when a physical Ctrl key is not available, you can usually use `Volume Down + C`.

## **How It Works**

```
Doctor URLs (one per provider)
    ↓
Detect provider from each URL
    ↓
Fetch public doctor page (per provider)
    ↓
Extract availability (per provider)
    ↓
Normalize into the shared appointment model
    ↓
Compare with that target's previous state
    ↓
New availability?
   ↙              ↘
 YES               NO
  ↓                 ↓
Telegram alert   Continue
(official link)
```

* Each provider + doctor pair has its **own** state entry, so one provider's availability can never suppress another provider's notifications.
* A local state file prevents duplicate notifications: the same open slots are reported once; when slots disappear the fingerprint resets, so a genuinely new slot triggers a new alert.
* If a provider fails repeatedly (about one hour of consecutive errors), a single Telegram warning is sent.

## **Configuration and Privacy**

The application stores its configuration in `~/.config/doctoreto-watcher/config.json` (the historical path is kept for compatibility).

```json
{
  "version": 2,
  "check_interval_seconds": 600,
  "bot_token": "...",
  "chat_id": "...",
  "targets": [
    {
      "provider": "doctoreto",
      "doctor_name": "نام دکتر",
      "doctor_id": "...",
      "doctor_url": "https://doctoreto.com/doctor/..."
    },
    {
      "provider": "paziresh24",
      "doctor_name": "نام دکتر",
      "doctor_id": "دکتر-...",
      "doctor_url": "https://paziresh24.com/dr/..."
    }
  ]
}
```

The configuration file is protected with restricted file permissions where supported.

**Never share or commit your configuration files or Telegram bot token.**

If a Telegram bot token is exposed, revoke it immediately using BotFather.

## **Troubleshooting**

| Symptom | What it means |
|---|---|
| `⚠️ Check failed: ...` in the comparison | That provider could not be checked (network, timeout, or the page structure changed). The other provider keeps working. |
| Telegram warning "چند بار پیاپی با خطا مواجه شده است" | A provider failed for about an hour of consecutive checks. Check your internet connection. |
| `Could not find ... The website structure may have changed.` | The provider's page no longer matches the expected structure; please open an issue. |
| No alert although slots are visible in the browser | The alert for those exact slots was already sent (duplicate prevention). Wait for a genuinely new slot, or delete `~/.config/doctoreto-watcher/state.json` to reset fingerprints. |
| `Configuration is incomplete` | Re-run `python appointment_watcher.py --setup`. |

## **Known Limitations**

* Doctoreto availability is read from the public page's embedded Next.js data; a website redesign can break parsing until the adapter is updated.
* Paziresh24 availability is read from the public page's embedded `samanDoctorAvailabilityStatus` data: it reports the nearest bookable time slot per clinic/online-visit, not the full list of all slots.
* Appointment prices are not exposed by either provider's public pages; the model keeps price *unknown* rather than guessing.
* Availability may change at any moment; the watcher reports what the page showed at check time and never books.

## **Security**

Never commit:

* Telegram bot tokens
* Private chat IDs
* Cookies or session credentials
* API keys
* Personal configuration files

This project does not require storing medical records or medical history, does not log in to any provider, and does not send credentials to any provider website.

## **Responsible Use**

This project is intended for personal appointment monitoring.

Users are responsible for complying with:

* Website Terms of Service
* Applicable rate limits
* Applicable laws and regulations
* Any restrictions imposed by the monitored platform

Use reasonable checking intervals (the default is 10 minutes) and avoid unnecessary traffic.

The project does not automatically book appointments.

## **Adding Support for a New Platform**

The application detects the platform automatically from the doctor URL.

Contributors can help by implementing the website-specific logic required to:

* Detect the platform (host names)
* Validate and normalize doctor URLs
* Identify the doctor
* Extract appointment information from the public page
* Normalize the result into the common appointment format

Shared monitoring, configuration and Telegram logic remains platform-independent. See `CONTRIBUTING.md` for guidelines and `providers/paziresh24.py` for a documented example adapter.

## **Contributing**

Contributions are welcome.

To add support for a new appointment platform:

1. Fork the repository
2. Create a feature branch
3. Implement the platform-specific logic in `providers/`
4. Add tests with captured page fixtures (`tests/fixtures/`)
5. Update the supported-platform documentation
6. Submit a Pull Request

Example:

```
git checkout -b feature/add-new-platform
```

Please keep platform-specific logic isolated and avoid unnecessary changes to the core application.

## **Project Structure**

```
doctor-appointment-watcher/
│
├── appointment_watcher.py       # CLI and orchestration entry point
├── providers/
│   ├── __init__.py              # Provider registry and URL detection
│   ├── base.py                  # Shared HTTP, Next.js helpers, provider contract
│   ├── model.py                 # Provider-neutral appointment model
│   ├── doctoreto.py             # Doctoreto adapter
│   └── paziresh24.py            # Paziresh24 adapter (documented data source)
├── tests/                       # unittest suite (offline, mocked HTTP)
│   ├── fixtures/                # Real captured provider pages
│   └── test_*.py
├── setup.sh
├── README.md
├── CONTRIBUTING.md
└── LICENSE
```

Run the tests with:

```
python -m unittest discover -s tests -t .
```

Tests are fully offline (HTTP and Telegram are mocked) and include real captured pages from both providers.

## **Roadmap**

* ~~Doctoreto support~~ ✅
* ~~Paziresh24 support~~ ✅
* ~~Automatic platform detection~~ ✅
* ~~Multi-provider adapter architecture~~ ✅
* ~~Automated tests~~ ✅
* More appointment platforms
* Cross-platform setup support

## **Disclaimer**

This project is an independent open-source project.

It is not affiliated with or endorsed by Doctoreto, Paziresh24 or any other supported appointment platform.

Appointment availability may change at any time.

The project does not guarantee successful appointment booking.

## **License**

This project is licensed under the MIT License.

See `LICENSE` for details.
