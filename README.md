Doctor Appointment Watcher

A lightweight Python tool that monitors supported doctor appointment pages and sends a Telegram notification when an available appointment is detected.

The project currently supports DoctorTo and is designed to gradually support more appointment platforms through community contributions.

Features

* Monitor a doctor’s appointment page automatically
* Detect newly available appointments
* Send Telegram notifications
* Avoid duplicate notifications for the same availability
* Persist configuration locally
* Reconfigure and monitor another doctor at any time
* Run continuously with a configurable checking interval
* Persian/Unicode doctor names are supported
* Termux-friendly
* Uses only the Python standard library
* Designed for community-driven support for additional platforms

Supported Platforms

Platform	Status
DoctorTo (doctoreto.com)	✅ Supported
Paziresh24 (paziresh24.com)	🚧 Planned
Other platforms	🤝 Community contributions welcome

Requirements

* Termux on Android
* Internet connection
* A supported doctor’s appointment page
* A Telegram bot

Python does not need to be installed manually. The setup script checks for Python and installs it automatically when necessary.

Installation

Clone the repository:

git clone https://github.com/hosseinzamaninasab/doctor-appointment-watcher.git
cd doctor-appointment-watcher

Run the setup script:

chmod +x setup.sh
./setup.sh

The setup script prepares the Termux environment and starts the application.

First Run

On the first run, no previous configuration exists, so the application automatically starts the initial setup.

You will be asked for:

1. Doctor appointment page URL
2. Doctor name (Persian is supported)
3. Telegram bot token
4. Telegram chat
5. Checking interval

Example:

=== Doctoreto Appointment Watcher Setup ===
Doctor URL:
> https://doctoreto.com/doctor/...
Doctor name:
> نام و نام خانوادگی دکتر
Telegram Bot Token:
> ********
Open your bot in Telegram and press Start.
Setup completed successfully.

The configuration is stored locally so you do not need to enter it every time.

Running the Watcher

After the initial setup, run:

./setup.sh

or:

python appointment_watcher.py

When an existing configuration is found, the application shows:

=== Doctoreto Appointment Watcher ===
1) Start watcher
2) Configure another doctor
3) Exit

Start Watcher

Select:

1

The current doctor will be monitored.

Configure Another Doctor

Select:

2

The previous doctor configuration is replaced with the new one and the previous monitoring state is reset.

This allows the same installation to be reused for another doctor without manually deleting configuration files.

Exit

Select:

3

Stop the Watcher

Press:

Ctrl + C

On Termux, when a physical Ctrl key is not available, you can usually use:

Volume Down + C

How It Works

Doctor URL
    ↓
Detect supported platform
    ↓
Fetch doctor page
    ↓
Extract doctor and appointment information
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

A small local state file is used to prevent duplicate notifications.

Configuration and Privacy

The application stores its configuration locally.

The configuration may contain:

* Doctor information
* Doctor URL
* Telegram bot token
* Telegram chat ID

The configuration file is protected with restricted file permissions where supported.

Never share or commit your configuration files or Telegram bot token.

If a Telegram bot token is exposed, revoke it immediately using BotFather.

Security

Never commit:

* Telegram bot tokens
* Private chat IDs
* Cookies
* Session credentials
* API keys
* Personal configuration files

This project does not require storing medical records or medical history.

Responsible Use

This project is intended for personal appointment monitoring.

Users are responsible for complying with:

* Website Terms of Service
* Applicable rate limits
* Applicable laws and regulations
* Any restrictions imposed by the monitored platform

Use reasonable checking intervals and avoid unnecessary traffic.

The project does not automatically book appointments.

Adding Support for a New Platform

The long-term goal is to support multiple appointment platforms while keeping the user experience simple.

Users should only need to provide a doctor URL. The application should detect the supported platform automatically.

Contributors can help by implementing the website-specific logic required to:

* Detect the platform
* Validate and normalize doctor URLs
* Identify the doctor
* Extract appointment information
* Determine availability
* Normalize the result into the common appointment format

Shared monitoring, configuration and Telegram logic should remain platform-independent.

See CONTRIBUTING.md for contribution guidelines.

Contributing

Contributions are welcome.

To add support for a new appointment platform:

1. Fork the repository
2. Create a feature branch
3. Implement the platform-specific logic
4. Test the integration
5. Update the supported-platform documentation
6. Submit a Pull Request

Example:

git checkout -b feature/add-new-platform

Please keep platform-specific logic isolated and avoid unnecessary changes to the core application.

Project Structure

doctor-appointment-watcher/
│
├── appointment_watcher.py
├── setup.sh
├── README.md
├── CONTRIBUTING.md
└── LICENSE

Roadmap

* DoctorTo support
* Telegram notifications
* Duplicate notification prevention
* Persistent configuration
* Reconfigure another doctor
* Termux setup script
* Automatic platform detection
* Paziresh24 support
* More appointment platforms
* Cleaner multi-platform adapter architecture
* Automated tests
* Improved error handling
* Cross-platform setup support

Disclaimer

This project is an independent open-source project.

It is not affiliated with or endorsed by DoctorTo or any other supported appointment platform.

Appointment availability may change at any time.

The project does not guarantee successful appointment booking.

License

This project is licensed under the MIT License.

See LICENSE for details.
