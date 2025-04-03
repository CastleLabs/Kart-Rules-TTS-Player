# Castle Fun Center - Go-Kart Announcement System

A Raspberry Pi-based announcement system for go-kart track safety announcements, featuring physical button triggers and a web-based configuration interface.

**Author:** Seth Morrow

## Table of Contents
- [Overview](#overview)
- [Features](#features)
- [System Components](#system-components)
  - [Button Monitoring Service (`kartrules.py`)](#button-monitoring-service-kartrulespy)
  - [Web Configuration Interface (`settings.py`)](#web-configuration-interface-settingspy)
  - [Configuration File (`config.ini`)](#configuration-file-configini)
- [Hardware Requirements](#hardware-requirements)
- [Raspberry Pi 4 Setup Instructions](#raspberry-pi-4-setup-instructions)
- [Wiring Guide](#wiring-guide)
- [Usage Instructions](#usage-instructions)
- [Troubleshooting](#troubleshooting)
- [Screenshots](#screenshots)
- [License](#license)

## Overview

This project provides a system for playing pre-recorded safety announcements on the Castle Fun Center's go-kart track using physical buttons connected to a Raspberry Pi. The system uses Text-to-Speech (TTS) technology to convert announcement text into natural-sounding voice announcements that are played through the Raspberry Pi's audio output.

The announcements are triggered by physical buttons for quick access by track staff:
- **Button 1**: Safety Rules - Plays the track rules and safety instructions
- **Button 2**: Remain Seated - Reminder for drivers to remain seated at all times
- **Button 3**: Other Announcement - Configurable for any other messages as needed

## Features

- **Physical Button Triggers**: Easy-to-access physical buttons for staff use
- **Web Configuration Interface**: Modern web UI for customizing announcements and settings
- **High-Quality Text-to-Speech**: Uses Microsoft Edge TTS for natural-sounding announcements
- **Audio Caching**: Pre-generates and caches audio files for instant playback
- **Multiple Voice Options**: Choose from various voices and accents
- **Real-time Testing**: Test announcements from the web interface before deploying
- **Status Monitoring**: View system status and logs through the web interface
- **Automatic Service Restart**: Configuration changes automatically restart the service
- **Debounce Protection**: Prevents accidental multiple triggers

## System Components

### Button Monitoring Service (`kartrules.py`)

A background service that monitors GPIO pins for button presses and plays the corresponding announcements:

- Runs continuously as a system service
- Loads configuration from `config.ini`
- Monitors GPIO pins for button presses
- Implements debouncing to prevent multiple triggers
- Generates speech from text using Edge TTS
- Caches audio files to improve performance
- Creates a lock file while playing to prevent overlapping announcements
- Logs all activities to `announcement_script.log`

### Web Configuration Interface (`settings.py`)

A Flask-based web application for configuring the system:

- Provides a user-friendly interface accessible via browser
- Allows editing of announcement texts for all buttons
- Offers voice selection and audio format configuration
- Enables real-time testing of announcements
- Displays system status indicators
- Provides access to system logs
- Includes service restart functionality
- Manages the TTS cache
- Uses the same locking mechanism as `kartrules.py`

### Configuration File (`config.ini`)

A simple text file storing key settings:

```ini
[announcements]
button1 = Safety rules announcement text goes here.
button2 = Please remain seated at all times.
button3 = Other announcement text goes here.

[tts]
voice_id = en-US-AndrewMultilingualNeural
output_format = mp3

[gpio]
button1 = 17
button2 = 27
button3 = 22
```

## Hardware Requirements

- Raspberry Pi 4 (2GB+ RAM recommended)
- SD card (16GB+ recommended) with Raspberry Pi OS
- Power supply for Raspberry Pi
- Active speakers or audio amplifier connected to the Raspberry Pi's audio output
- 3 momentary push buttons
- Wires for connecting buttons to GPIO pins
- Optional: Case for Raspberry Pi and buttons

## Raspberry Pi 4 Setup Instructions

These instructions assume you are starting with a Raspberry Pi 4 running Raspberry Pi OS (or a similar Debian-based Linux distribution) and have terminal (SSH or direct) access.

### Step 1: System Update & Prerequisites

First, update your system's package list and install necessary tools:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv mpg123 git
```

- `python3`, `python3-pip`, `python3-venv`: Ensure Python 3 and its package manager/virtual environment tools are installed.
- `mpg123`: The command-line audio player used by the scripts.
- `git`: Useful for potentially cloning the project repository.

### Step 2: Create Project Directory and User (Optional but Recommended)

It's good practice to run services under a dedicated user and directory.

```bash
# Create a directory for the application
sudo mkdir /opt/karts
# Create a user for the application (no password, system account)
sudo useradd --system --group --shell /bin/false karts
# Set ownership of the directory
sudo chown -R karts:karts /opt/karts
```

### Step 3: Place Project Files

Transfer all the project files into the `/opt/karts` directory. Ensure the karts user owns them:

```bash
# Example: If files are in the current user's home directory in a folder 'KartsFINAL'
sudo cp -r ~/KartsFINAL/* /opt/karts/
sudo chown -R karts:karts /opt/karts
```

### Step 4: Create Python Virtual Environment and Install Dependencies

Navigate to the project directory and set up a virtual environment for Python packages.

```bash
cd /opt/karts
# Create virtual environment as the 'karts' user
sudo -u karts python3 -m venv .venv
# Activate environment (temporarily in current shell for installing packages)
source .venv/bin/activate
# Install Python libraries using pip (as 'karts' user)
sudo -u karts .venv/bin/pip install RPi.GPIO edge-tts Flask
# Deactivate environment (we'll specify the full path in the service file)
deactivate
```

### Step 5: Configure Audio Output

Ensure the Raspberry Pi's audio output is configured correctly (e.g., HDMI or the 3.5mm jack). You can use raspi-config:

```bash
sudo raspi-config
```

Navigate to `System Options` -> `Audio` and select the desired output.

### Step 6: Create Systemd Service Files

Create the first service file for the button monitoring script:

```bash
sudo nano /etc/systemd/system/kartrules.service
```

Paste the following content:

```ini
[Unit]
Description=Go-Kart Rules Button Monitor Service
After=network.target sound.target

[Service]
User=karts
Group=karts
WorkingDirectory=/opt/karts
# Execute the script using the Python interpreter inside the virtual environment
ExecStart=/opt/karts/.venv/bin/python /opt/karts/kartrules.py
Restart=always
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Create the second service file for the web settings interface:

```bash
sudo nano /etc/systemd/system/kartsettings.service
```

Paste the following content:

```ini
[Unit]
Description=Go-Kart Rules Web Settings Service
After=network.target kartrules.service
BindsTo=kartrules.service

[Service]
User=karts
Group=karts
WorkingDirectory=/opt/karts
# Execute the Flask app using the Python interpreter inside the virtual environment
ExecStart=/opt/karts/.venv/bin/python /opt/karts/settings.py
Restart=always
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Step 7: Enable and Start the Services

```bash
sudo systemctl daemon-reload
sudo systemctl enable kartrules.service
sudo systemctl enable kartsettings.service
sudo systemctl start kartrules.service
sudo systemctl start kartsettings.service
```

### Step 8: Verify Operation

Check Service Status:

```bash
sudo systemctl status kartrules.service
sudo systemctl status kartsettings.service
# View logs
sudo journalctl -u kartrules.service -f
sudo journalctl -u kartsettings.service -f
```

## Wiring Guide

Connect momentary push buttons between the configured GPIO pins and a Ground (GND) pin on the Raspberry Pi. The `kartrules.py` script uses the internal pull-up resistors (`pull_up_down=GPIO.PUD_UP`), so you only need the button and wires.

**Default Pins (Check config.ini):**
- Button 1 (Safety Rules): BCM Pin 17
- Button 2 (Remain Seated): BCM Pin 27
- Button 3 (Other Announcement): BCM Pin 22

Connect one side of each button to the respective GPIO pin and the other side to any GND pin.

![GPIO Pinout Diagram](https://pinout.xyz/resources/raspberry-pi-pinout.png)

## Usage Instructions

### Accessing the Web Interface

1. Find your Raspberry Pi's IP address using `hostname -I` in the terminal
2. Open a web browser on any device on the same network
3. Navigate to `http://<RaspberryPi_IP>:5000`

### Configuring Announcements

1. Log in to the web interface
2. Edit the text for each button as needed
3. Select the desired voice from the dropdown menu
4. Click "Save Configuration" - the service will automatically restart
5. Use the "Test" buttons to preview the announcements

### Using the Physical Buttons

Simply press the physical buttons connected to the Raspberry Pi to play the corresponding announcements through the configured audio output.

## Troubleshooting

### Common Issues

1. **No audio output:**
   - Check that the correct audio output is selected in `raspi-config`
   - Ensure speakers are connected and powered on
   - Verify audio is not muted (`alsamixer` command)

2. **Buttons not responding:**
   - Check the wiring connections
   - Verify the correct GPIO pins are configured in `config.ini`
   - Check the system logs for errors: `sudo journalctl -u kartrules.service -f`

3. **Web interface not accessible:**
   - Ensure the Raspberry Pi is connected to the network
   - Verify that the `kartsettings.service` is running: `sudo systemctl status kartsettings.service`
   - Check for firewall issues that might be blocking port 5000

4. **Failed to generate speech:**
   - Ensure `edge-tts` is installed correctly
   - Check internet connection (needed for initial TTS setup)
   - Review logs for specific errors

### Viewing Logs

Through the web interface:
1. Access the web UI
2. Click the "View Logs" button on the System Status card

Through the terminal:
```bash
sudo journalctl -u kartrules.service -f
sudo journalctl -u kartsettings.service -f
cat /opt/karts/announcement_script.log
```



## License

This project is provided for Castle Fun Center. All rights reserved.

---

© 2025 Castle Fun Center - Go-Kart Announcement System
