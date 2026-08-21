#!/data/data/com.termux/files/usr/bin/bash

set -e

echo "Checking Termux environment..."

if ! command -v pkg >/dev/null 2>&1; then
    echo "Error: This setup script is intended for Termux."
    exit 1
fi

echo "Updating package information..."
pkg update -y

if ! command -v python >/dev/null 2>&1; then
    echo "Python is not installed. Installing Python..."
    pkg install python -y
else
    echo "Python is already installed:"
    python --version
fi

echo
echo "Starting Doctor Appointment Watcher..."
echo

python appointment_watcher.py
