# Doctor Appointment Watcher

A lightweight Python tool that monitors supported doctor appointment pages and sends a Telegram notification when an available appointment is detected.

The project currently supports **DoctorTo** and is designed to make adding other appointment platforms easy through community contributions.

## Features

- Monitor a doctor's appointment page automatically
- Detect newly available appointments
- Send notifications through Telegram
- Avoid sending duplicate notifications for the same availability
- Run continuously at a configurable interval
- Simple CLI configuration
- Designed for easy addition of new supported websites
- No database required

## Current Supported Platforms

| Platform | Status |
|---|---|
| DoctorTo (`doctoreto.com`) | ✅ Supported |
| پذیرش24 (`paziresh24.com`) | 🚧 Planned |
| Other platforms | 🤝 Community contributions welcome |

## Requirements

- Python 3.10+
- Internet connection
- Telegram Bot
- A supported doctor's appointment page

## Installation

Clone the repository:

```bash
git clone https://github.com/hosseinzamaninasab/doctor-appointment-watcher.git
cd doctor-appointment-watcher
Install dependencies:
pip install -r requirements.txt
The program will guide you through the required configuration.
You provide:
1. Doctor appointment page URL
2. Doctor name
3. Telegram bot token
4. Telegram chat ID
5. Check interval
⠀ After configuration, the watcher periodically checks the appointment page.
When an available appointment is detected, a Telegram notification is sent.
Example
Doctor Appointment Watcher

Supported platforms:
  1. DoctorTo

Doctor URL:
> https://doctoreto.com/doctor/...

Doctor name:
> دکتر علی سعیدپور پاریزی

Telegram bot token:
> ********

Telegram chat ID:
> ********

Check interval (seconds):
> 60

Watcher started.
Checking for available appointments...
doctor-appointment-watcher/
│
├── appointment_watcher.py
├── requirements.txt
├── README.md
├── LICENSE
└── CONTRIBUTING.md
How It Works
Doctor URL
    ↓
Detect supported platform
    ↓
Fetch appointment page
    ↓
Extract appointment information
    ↓
Check availability
    ↓
Compare with previous state
    ↓
New appointment?
   ↙       ↘
 YES        NO
  ↓          ↓
Telegram    Continue
notification
The watcher stores a small amount of state locally to prevent repeatedly notifying the user about the same appointment.
Adding a New Platform
The goal of this project is to support multiple appointment platforms without making the user learn how each platform works.
Contributors can add support for a new website by implementing the platform-specific extraction logic while keeping the main user experience unchanged.
A new platform should ideally require:
URL detection
Appointment extraction
Availability detection
Normalized appointment data The core watcher should remain platform-independent.
See:
CONTRIBUTING.md
for contribution guidelines.
Contributing
Contributions are welcome!
If you want to add support for another doctor appointment platform:
1. Fork the repository
2. Create a new branch
3. Implement the platform adapter
4. Add tests where possible
5. Update the supported-platform documentation
6. Submit a Pull Request
⠀ Example:
git checkout -b feature/add-new-platform
Please keep platform-specific logic isolated from the core watcher.
Security & Privacy
This project requires a Telegram bot token.
Treat the token as a password.
Do not:
Commit bot tokens
Share tokens publicly
Put tokens directly into source code
Upload configuration files containing secrets If a token is accidentally exposed, immediately revoke it using BotFather and generate a new one.
The project does not require storing medical information.
Responsible Use
This project is intended for personal appointment monitoring.
Users are responsible for complying with the terms of service, robots.txt policies where applicable, rate limits, and applicable laws of the websites they monitor.
Please use reasonable checking intervals and avoid unnecessary requests to appointment platforms.
Disclaimer
This project is an independent open-source project and is not affiliated with or endorsed by DoctorTo or any other supported appointment platform.
Appointment availability is determined from publicly accessible website information and may change at any time.
The project does not guarantee successful appointment booking.
Roadmap
DoctorTo support
Telegram notifications
Duplicate notification prevention
Interactive platform selection
Automatic platform detection
Add Paziresh24 support
Add more appointment platforms
Platform adapter architecture
Automated tests
Better error handling
Docker support
Configuration via environment variables
License
This project is licensed under the MIT License.
See LICENSE for details.
