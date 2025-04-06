#!/usr/bin/env python3
"""
Written by Seth Morrow

This script handles GPIO events for the physical buttons on the Raspberry Pi.
It plays announcements using TTS synthesis and cached audio files.
The script uses a rotating log to ensure log file sizes remain bounded.
"""

import asyncio
import edge_tts
import logging
import sys
import os
import subprocess
import time
import hashlib
import threading
from pathlib import Path
from logging.handlers import RotatingFileHandler  # Import rotating logs handler

# Configure rotating logging
rotating_handler = RotatingFileHandler("announcement_script.log", maxBytes=5000000, backupCount=2)
rotating_handler.setLevel(logging.DEBUG)
rotating_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logging.basicConfig(
    level=logging.DEBUG,
    handlers=[rotating_handler, logging.StreamHandler(sys.stdout)]
)

# Path to a lock file used to indicate an announcement is playing
ANNOUNCEMENT_LOCK_FILE = "/tmp/announcement_playing.lock"

# Directory where TTS audio files are cached
CACHE_DIR = "/tmp/tts_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# Dictionary to track the last announcement time for each button
last_button_press = {
    "button1": 0,
    "button2": 0,
    "button3": 0,
    "button4": 0   # Button 4 plays the pre-existing Yiddish announcement
}

# Minimum time between announcements for the same button (in seconds)
DEBOUNCE_TIMEOUT = 8.0

# Threading lock to ensure only one button press is processed at a time
button_handler_lock = threading.Lock()


class Config:
    """
    Configuration object to hold announcement texts, TTS settings, and GPIO assignments.
    """
    def __init__(self):
        self.announcements = {
            "button1": "",
            "button2": "",
            "button3": "",
            "button4": ""
        }
        self.tts = {
            "voice_id": "",
            "output_format": "mp3"
        }
        self.gpio = {
            "button1": 17,
            "button2": 27,
            "button3": 22,
            "button4": 23
        }
        self.last_modified = 0


def is_announcement_playing():
    """
    Check if an announcement is currently playing by verifying the lock file's existence.
    """
    return os.path.exists(ANNOUNCEMENT_LOCK_FILE)


def set_announcement_playing(is_playing=True):
    """
    Create or remove the lock file based on the announcement playing status.
    """
    if is_playing:
        with open(ANNOUNCEMENT_LOCK_FILE, 'w') as f:
            f.write(str(time.time()))
    else:
        if os.path.exists(ANNOUNCEMENT_LOCK_FILE):
            try:
                os.remove(ANNOUNCEMENT_LOCK_FILE)
            except Exception as e:
                logging.warning(f"Failed to remove lock file: {e}")


def get_cache_filename(text: str, voice_id: str):
    """
    Generate a unique filename for caching synthesized speech based on text and voice ID.
    """
    hash_object = hashlib.md5((text + voice_id).encode())
    hash_str = hash_object.hexdigest()
    return os.path.join(CACHE_DIR, f"{hash_str}.mp3")


def load_config(config_path: str = "config.ini") -> Config:
    """
    Load the configuration from the config file.
    """
    config = Config()
    current_section = None
    try:
        if not os.path.exists(config_path):
            logging.error(f"Config file not found: {config_path}")
            raise FileNotFoundError(f"Config file not found: {config_path}")
        config.last_modified = os.path.getmtime(config_path)
        with open(config_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('[') and line.endswith(']'):
                    current_section = line[1:-1].lower()
                    continue
                if '=' not in line:
                    continue
                key, value = [x.strip() for x in line.split('=', 1)]
                clean_value = value.strip('"\'')
                if current_section == 'announcements':
                    if key.lower() in config.announcements:
                        config.announcements[key.lower()] = clean_value
                elif current_section == 'tts':
                    if key.lower() == 'voice_id':
                        config.tts['voice_id'] = clean_value
                    elif key.lower() == 'output_format':
                        config.tts['output_format'] = clean_value.lower()
                elif current_section == 'gpio':
                    try:
                        config.gpio[key.lower()] = int(clean_value)
                    except ValueError:
                        logging.warning(f"Invalid GPIO pin value for {key}: {clean_value}")
        if not config.tts['voice_id']:
            raise ValueError("Missing required TTS voice_id configuration")
        logging.info("Configuration loaded successfully")
        return config
    except Exception as e:
        logging.error(f"Error loading config: {e}")
        raise


async def synthesize_speech_async(text: str, voice_id: str, output_path: str) -> bool:
    """
    Asynchronously synthesize speech using edge_tts.
    """
    try:
        logging.info(f"Synthesizing speech: {text[:50]}...")
        communicate = edge_tts.Communicate(text, voice_id)
        await communicate.save(output_path)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logging.info("Speech synthesis successful")
            return True
        else:
            logging.error("Speech synthesis failed - output file empty or missing")
            return False
    except Exception as e:
        logging.error(f"Error during speech synthesis: {e}")
        return False


def play_sound(sound_path: str, output_format: str) -> bool:
    """
    Play the specified audio file using mpg123.
    """
    if not sound_path or not os.path.exists(sound_path):
        logging.error(f"Invalid sound path: {sound_path}")
        return False
    try:
        logging.info(f"Playing sound file: {sound_path}")
        if subprocess.run(['which', 'mpg123'], capture_output=True).returncode != 0:
            logging.error("mpg123 is not installed")
            return False
        subprocess.run(['mpg123', '-q', sound_path], check=True)
        logging.info("Sound played successfully")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Error playing sound: {e}")
        return False
    except Exception as e:
        logging.error(f"Error playing sound: {e}")
        return False
    finally:
        set_announcement_playing(False)


def handle_button_press(button_id: str, config: Config):
    """
    Process a button press and play the corresponding announcement.
    """
    global last_button_press
    with button_handler_lock:
        if is_announcement_playing():
            logging.info(f"Button {button_id} press ignored - announcement already playing")
            return
        current_time = time.time()
        if current_time - last_button_press.get(button_id, 0) < DEBOUNCE_TIMEOUT:
            logging.info(f"Button {button_id} press ignored - too soon after previous press")
            return
        set_announcement_playing(True)
        if button_id == "button4":
            logging.info("Button 4 pressed. Playing Yiddish announcement.")
            last_button_press[button_id] = current_time
            if not play_sound("/home/tech/yiddish.mp3", "mp3"):
                logging.error("Failed to play Yiddish announcement for button4")
            set_announcement_playing(False)
            return
        last_button_press[button_id] = current_time
        announcement_text = config.announcements.get(button_id, "")
        if not announcement_text:
            logging.error(f"No announcement configured for {button_id}")
            set_announcement_playing(False)
            return
        cache_file = get_cache_filename(announcement_text, config.tts['voice_id'])
        if not os.path.exists(cache_file) or os.path.getsize(cache_file) == 0:
            logging.info(f"Cache miss - generating new speech file for button {button_id}")
            success = asyncio.run(synthesize_speech_async(announcement_text, config.tts['voice_id'], cache_file))
            if not success:
                if os.path.exists(cache_file):
                    os.remove(cache_file)
                logging.error(f"Failed to synthesize speech for button {button_id}")
                set_announcement_playing(False)
                return
        logging.info(f"Button {button_id} pressed. Playing announcement.")
        if not play_sound(cache_file, config.tts['output_format']):
            logging.error(f"Failed to play announcement for {button_id}")
            set_announcement_playing(False)


def make_callback(button_id: str, config: Config):
    """
    Create a GPIO callback function for a specific button.
    """
    def callback(channel):
        logging.debug(f"GPIO event detected for button {button_id}")
        time.sleep(0.005)
        if GPIO.input(config.gpio[button_id]) == GPIO.LOW:
            if not is_announcement_playing():
                threading.Thread(target=handle_button_press, args=(button_id, config), daemon=True).start()
            else:
                logging.info(f"Button {button_id} press ignored because an announcement is playing")
        else:
            logging.debug(f"False trigger ignored for button {button_id} after delay")
    return callback


def setup_gpio(config: Config):
    """
    Configure GPIO pins and register event detection callbacks.
    """
    try:
        GPIO.cleanup()
    except Exception as e:
        logging.debug(f"GPIO cleanup error: {e}")
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for button_key, pin in config.gpio.items():
        try:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        except Exception as e:
            logging.error(f"Failed to setup pin {pin} for {button_key}: {e}")
            continue
        try:
            GPIO.remove_event_detect(pin)
        except Exception as e:
            logging.debug(f"No existing event detection on pin {pin}: {e}")
        try:
            callback = make_callback(button_key, config)
            GPIO.add_event_detect(pin, GPIO.FALLING, callback=callback, bouncetime=500)
        except RuntimeError as e:
            logging.error(f"Failed to add edge detection for pin {pin}: {e}")
    logging.info("GPIO setup complete; waiting for button presses...")


def pre_generate_announcements(config: Config):
    """
    Pre-generate TTS announcement files for all configured buttons.
    """
    logging.info("Pre-generating announcement files...")
    for button_id, text in config.announcements.items():
        if text:
            logging.info(f"Pre-generating announcement for {button_id}...")
            cache_file = get_cache_filename(text, config.tts['voice_id'])
            if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
                logging.info(f"Speech file for {button_id} already exists")
                continue
            logging.info(f"Generating new speech file for {button_id}")
            success = asyncio.run(synthesize_speech_async(text, config.tts['voice_id'], cache_file))
            if success:
                logging.info(f"Generated speech file for {button_id}")
            else:
                if os.path.exists(cache_file):
                    os.remove(cache_file)
                logging.error(f"Failed to generate speech file for {button_id}")
    logging.info("Finished pre-generating announcements")


def cleanup():
    """
    Clean up resources on exit by removing the lock file and resetting GPIO.
    """
    set_announcement_playing(False)
    GPIO.cleanup()


def main():
    """
    Main function to load configuration, set up GPIO, and monitor for config changes.
    """
    set_announcement_playing(False)
    config_path = "config.ini"
    last_checked_time = 0
    try:
        config = load_config(config_path)
        pre_generate_announcements(config)
    except Exception as e:
        logging.error("Failed to load config. Exiting.")
        sys.exit(1)
    setup_gpio(config)
    try:
        while True:
            current_time = time.time()
            if current_time - last_checked_time > 10:
                last_checked_time = current_time
                if os.path.exists(config_path):
                    mod_time = os.path.getmtime(config_path)
                    if mod_time > config.last_modified:
                        logging.info("Config file changed, reloading...")
                        new_config = load_config(config_path)
                        regenerate = False
                        if new_config.tts['voice_id'] != config.tts['voice_id']:
                            logging.info("Voice ID changed, regenerating all announcements")
                            regenerate = True
                        else:
                            for button_id, text in new_config.announcements.items():
                                if text != config.announcements.get(button_id, ""):
                                    logging.info(f"Announcement for {button_id} changed")
                                    regenerate = True
                        config = new_config
                        setup_gpio(config)
                        if regenerate:
                            pre_generate_announcements(config)
            time.sleep(0.1)
    except KeyboardInterrupt:
        logging.info("Shutdown requested. Cleaning up GPIO.")
        cleanup()


if __name__ == '__main__':
    try:
        import RPi.GPIO as GPIO
    except ImportError:
        logging.error("RPi.GPIO module not found. This code must run on a Raspberry Pi.")
        sys.exit(1)
    main()
