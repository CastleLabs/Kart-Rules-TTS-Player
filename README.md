# Castle Fun Center - Go-Kart Announcement System

A Raspberry Pi‑based announcement system for go‑kart track safety announcements featuring physical button triggers, a modern web‑based configuration interface, and enhanced logging and audio playback features.

**Author:** Seth Morrow

## Table of Contents
- [Overview](#overview)
- [Features](#features)
- [System Components](#system-components)
  - [Button Monitoring Service](#button-monitoring-service-kartrulespy)
  - [Web Configuration Interface](#web-configuration-interface-settingspy)
  - [Configuration File](#configuration-file-configini)
  - [Web UI Templates](#web-ui-templates)
  - [Client-side Script](#client-side-script-mainjs)
  - [Stylesheet](#stylesheet-stylecss)
- [Hardware Requirements](#hardware-requirements)
- [Raspberry Pi 4 Setup Instructions](#raspberry-pi-4-setup-instructions)
- [Wiring Guide](#wiring-guide)
- [Usage Instructions](#usage-instructions)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## Overview
This project is a robust, Raspberry Pi‑based system designed for rapid safety announcement playback on the Castle Fun Center's go‑kart track. It combines physical button triggers with a web‑based configuration interface to allow track staff to quickly play announcements. Recent updates include:

- **Rotating Logs:** Log files are now managed using a rotating log handler to keep file sizes in check.
- **Dedicated Yiddish Announcement:** A new button and corresponding web endpoint to play a pre‑recorded Yiddish MP3.
- **Enhanced TTS Caching:** Improved caching and pre‑generation of TTS audio files to ensure near‑instant playback.

## Features
- **Physical Button Triggers:** Quick access through dedicated hardware buttons.
- **Web Configuration Interface:** Configure announcement texts and settings via an intuitive web UI.
- **High‑Quality Text‑to‑Speech:** Converts announcement text to speech using Microsoft Edge TTS.
- **Audio Caching & Pre‑generation:** Automatically generates and caches audio files to minimize playback delay.
- **Rotating Logs:** Uses a rotating log mechanism to manage log file sizes and prevent disk bloat.
- **Dedicated Yiddish MP3 Playback:** A special button triggers playback of a pre‑recorded Yiddish announcement.
- **Service Auto‑Restart:** Services automatically restart upon configuration changes.
- **Debounce Protection:** Ensures only one announcement is triggered per button press.
- **Real‑time Status Monitoring:** Displays system status and allows viewing/downloading of logs through the web interface.

## System Components

### Button Monitoring Service (kartrules.py)
**Role:** Monitors GPIO pins for button presses and plays the corresponding announcements.

**Key Points:**
- Loads configuration from config.ini.
- Uses internal debouncing to prevent duplicate triggers.
- Generates TTS audio files using Edge TTS and caches them in /tmp/tts_cache.
- Implements a lock file mechanism to prevent overlapping announcements.
- Now utilizes a rotating log handler to limit log file size.

### Web Configuration Interface (settings.py)
**Role:** Provides a Flask‑based web UI for managing and testing announcements.

**Key Points:**
- Allows editing of announcement texts, including for button4 (Yiddish announcement).
- Supports TTS configuration (voice ID and output format).
- Includes a test interface with dedicated buttons for each announcement, including one for playing the Yiddish MP3.
- Provides system status, log viewing, cache management, and service restart functionality.
- Uses the same rotating log mechanism as the button service.

### Configuration File (config.ini)
Stores all key settings including announcement texts, TTS settings, and GPIO pin assignments. For example:

```ini
[announcements]
button1 = To ride the Road Course you must be at least 54 inches tall.
button2 = Please remain seated, DO NOT get up until a staff member tells you to.
button3 = This is an extra announcement button.
button4 = # This button will play the pre-existing Yiddish announcement.

[tts]
voice_id = en-US-AndrewMultilingualNeural
output_format = mp3

[gpio]
button1 = 17
button2 = 27
button3 = 22
button4 = 23
```

### Web UI Templates
**config.html:**
Contains the web interface for configuring and testing announcements. It includes a new test button for playing the Yiddish MP3 and displays real‑time system status and logs.

**error.html:**
Provides custom error pages for handling 404 and 500 errors gracefully.

### Client-side Script (main.js)
Handles dynamic behavior of the web UI, including:
- Periodic status checking.
- Form validation and flash message auto‑hide.
- Event listeners for test buttons, including a new handler for the Yiddish announcement.

### Stylesheet (style.css)
Provides modern, professional styling for the UI, ensuring a responsive and user‑friendly experience across devices.

## Hardware Requirements
- Raspberry Pi 4 (2GB+ RAM recommended)
- SD card (16GB+ recommended) with Raspberry Pi OS
- Power supply for Raspberry Pi
- Active speakers or audio amplifier connected to the Pi's audio output
- 3 momentary push buttons (physical triggers)
- Wires for connecting buttons to GPIO pins
- Optional: Protective case for the Raspberry Pi and buttons

## Raspberry Pi 4 Setup Instructions
These instructions assume you are starting with a Raspberry Pi 4 running Raspberry Pi OS with terminal (SSH or direct) access.

### Step 1: System Update & Prerequisites
```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv mpg123 git
```

### Step 2: Create Project Directory and Dedicated User
```bash
sudo mkdir /opt/karts
sudo useradd --system --group --shell /bin/false karts
sudo chown -R karts:karts /opt/karts
```

### Step 3: Place Project Files
Copy all project files into /opt/karts and adjust ownership:
```bash
sudo cp -r ~/YourProjectFolder/* /opt/karts/
sudo chown -R karts:karts /opt/karts
```

### Step 4: Create Python Virtual Environment & Install Dependencies
```bash
cd /opt/karts
sudo -u karts python3 -m venv .venv
source .venv/bin/activate
sudo -u karts .venv/bin/pip install RPi.GPIO edge-tts Flask
deactivate
```

### Step 5: Configure Audio Output
Ensure the Raspberry Pi's audio output is set correctly (e.g., HDMI or 3.5mm jack):
```bash
sudo raspi-config
```
Select System Options → Audio and choose the desired output.

### Step 6: Create Systemd Service Files
For the Button Monitoring Service:
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
ExecStart=/opt/karts/.venv/bin/python /opt/karts/kartrules.py
Restart=always
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

For the Web Configuration Interface:
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
Check the status of each service:
```bash
sudo systemctl status kartrules.service
sudo systemctl status kartsettings.service
sudo journalctl -u kartrules.service -f
sudo journalctl -u kartsettings.service -f
```

## Wiring Guide
Connect each momentary push button between its designated GPIO pin and a Ground (GND) pin. The system uses internal pull‑up resistors, so only a connection to GND is needed.

Default GPIO Assignments (see config.ini):
- Button 1 (Safety Rules): BCM 17
- Button 2 (Remain Seated): BCM 27
- Button 3 (Other Announcement): BCM 22
- Button 4 (Yiddish Announcement): BCM 23

Refer to the Raspberry Pi GPIO pinout diagram for proper wiring.

## Usage Instructions

### Accessing the Web Interface
Determine your Raspberry Pi's IP address using:
```bash
hostname -I
```
Open a web browser on a device in the same network.

Navigate to http://<RaspberryPi_IP>:5000.

### Configuring Announcements
1. Log in to the web interface.
2. Edit the announcement texts for each button.
3. The Yiddish announcement (Button 4) is configured to play a pre‑recorded MP3 located at /home/tech/yiddish.mp3.
4. Click "Save Configuration" to apply changes; the system will automatically restart if needed.
5. Use the test buttons to preview announcements, including the dedicated Yiddish test button.

### Using the Physical Buttons
Press the physical buttons connected to the Raspberry Pi to trigger the corresponding announcements through the configured audio output.

## Troubleshooting

### Common Issues
**No Audio Output:**
- Ensure the correct audio output is selected via raspi-config.
- Verify that speakers or an amplifier are connected and powered.
- Check volume settings using alsamixer.

**Buttons Not Responding:**
- Verify wiring connections and correct GPIO pin assignments in config.ini.
- Check service logs using:
```bash
sudo journalctl -u kartrules.service -f
```

**Web Interface Inaccessible:**
- Ensure the Raspberry Pi is connected to the network.
- Verify that the kartsettings.service is running:
```bash
sudo systemctl status kartsettings.service
```

**Permission Errors for TTS Cache:**
- Since security is not a primary concern, ensure the TTS cache directory is world‑writable:
```bash
sudo chmod -R 777 /tmp/tts_cache
```

**TTS Synthesis Failures:**
- Verify that edge-tts is installed correctly.
- Ensure the Raspberry Pi has a stable internet connection for TTS services.
- Review logs for specific DNS or connectivity errors.

### Viewing Logs
**Web Interface:**
Click the "View Logs" button on the System Status card.

**Terminal:**
```bash
sudo journalctl -u kartrules.service -f
sudo journalctl -u kartsettings.service -f
cat /opt/karts/announcement_script.log
```

### New Features Overview
**Rotating Logs:**
The system now employs a rotating log handler (configured in both kartrules.py and settings.py) to manage log file sizes and prevent disk usage issues.

**Dedicated Yiddish Announcement:**
A new button in the web interface and a corresponding GPIO configuration (Button 4) have been added to play a pre‑recorded Yiddish MP3 located at /home/tech/yiddish.mp3.

**Enhanced TTS Caching:**
Improved caching and pre‑generation of TTS audio files ensure faster playback.

## License
This project is provided for Castle Fun Center. All rights reserved.
