#!/usr/bin/env python3
import asyncio
import edge_tts
import logging
import sys
import os
import subprocess
import tempfile
import time
import hashlib
import threading
from pathlib import Path

# Import GPIO for Raspberry Pi
try:
    import RPi.GPIO as GPIO
except ImportError:
    logging.error("RPi.GPIO module not found. This code must run on a Raspberry Pi.")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("announcement_script.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

# Shared state file path
ANNOUNCEMENT_LOCK_FILE = "/tmp/announcement_playing.lock"

# Directory for cached TTS files
CACHE_DIR = "/tmp/tts_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# Global dictionary to track last announcement time for each button
last_button_press = {
    "button1": 0,
    "button2": 0,
    "button3": 0
}

# Minimum time between announcements in seconds (8 seconds between same button presses)
DEBOUNCE_TIMEOUT = 8.0

# Add a lock to protect access to the button handlers
button_handler_lock = threading.Lock()

class Config:
    def __init__(self):
        # Announcement texts for each button
        self.announcements = {
            "button1": "",
            "button2": "",
            "button3": ""
        }
        # TTS configuration
        self.tts = {
            "voice_id": "",
            "output_format": "mp3"
        }
        # GPIO pin assignments (default values can be overridden via config.ini)
        self.gpio = {
            "button1": 17,
            "button2": 27,
            "button3": 22
        }
        # Last modified time of config file
        self.last_modified = 0

def is_announcement_playing():
    """Check if an announcement is currently playing by looking for the lock file."""
    return os.path.exists(ANNOUNCEMENT_LOCK_FILE)

def set_announcement_playing(is_playing=True):
    """Set whether an announcement is currently playing."""
    if is_playing:
        # Create the lock file
        with open(ANNOUNCEMENT_LOCK_FILE, 'w') as f:
            f.write(str(time.time()))
    else:
        # Remove the lock file if it exists
        if os.path.exists(ANNOUNCEMENT_LOCK_FILE):
            try:
                os.remove(ANNOUNCEMENT_LOCK_FILE)
            except Exception as e:
                logging.warning(f"Failed to remove lock file: {e}")

def get_cache_filename(text: str, voice_id: str):
    """Generate a unique cache filename based on text and voice_id."""
    # Create a hash of the text and voice_id to use as the filename
    hash_object = hashlib.md5((text + voice_id).encode())
    hash_str = hash_object.hexdigest()
    return os.path.join(CACHE_DIR, f"{hash_str}.mp3")

def load_config(config_path: str = "config.ini") -> Config:
    config = Config()
    current_section = None
    
    try:
        if not os.path.exists(config_path):
            logging.error(f"Config file not found: {config_path}")
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        # Get file modification time
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
    if not sound_path or not os.path.exists(sound_path):
        logging.error(f"Invalid sound path: {sound_path}")
        return False
    try:
        logging.info(f"Playing sound file: {sound_path}")
        if subprocess.run(['which', 'mpg123'], capture_output=True).returncode != 0:
            logging.error("mpg123 is not installed")
            return False
        
        # Block further button handling during playback by keeping the lock file
        
        # Play the sound - this is a blocking call
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
        # Clear the flag when done playing
        set_announcement_playing(False)

def handle_button_press(button_id: str, config: Config):
    global last_button_press
    
    # Use this lock to prevent multiple button handlers from running simultaneously
    # This ensures only one button press can be processed at a time
    with button_handler_lock:
        # Double-check if any announcement is already playing
        # This is critical for preventing queued announcements
        if is_announcement_playing():
            logging.info(f"Button {button_id} press ignored - announcement already playing")
            return
        
        # Get current time
        current_time = time.time()
        
        # Check if enough time has passed since the last press of this button
        if current_time - last_button_press[button_id] < DEBOUNCE_TIMEOUT:
            logging.info(f"Button {button_id} press ignored - too soon after previous press")
            return
        
        # CRITICAL: Set the lock file BEFORE starting any processing
        # This will prevent other button presses from being processed during this time
        set_announcement_playing(True)
        
        try:
            # Update the last press time for this button
            last_button_press[button_id] = current_time
            
            # Process the announcement
            announcement_text = config.announcements.get(button_id, "")
            if not announcement_text:
                logging.error(f"No announcement configured for {button_id}")
                set_announcement_playing(False)  # Release the lock if no announcement
                return
            
            # Get the cached file path
            cache_file = get_cache_filename(announcement_text, config.tts['voice_id'])
            
            # Check if we need to generate the file
            if not os.path.exists(cache_file) or os.path.getsize(cache_file) == 0:
                # File doesn't exist or is empty, generate it
                logging.info(f"Cache miss - generating new speech file for button {button_id}")
                success = asyncio.run(synthesize_speech_async(announcement_text, config.tts['voice_id'], cache_file))
                if not success:
                    if os.path.exists(cache_file):
                        os.remove(cache_file)
                    logging.error(f"Failed to synthesize speech for button {button_id}")
                    set_announcement_playing(False)  # Make sure to release lock if synthesis fails
                    return
            
            # Play the sound (the lock file will be cleared in play_sound)
            logging.info(f"Button {button_id} pressed. Playing announcement.")
            if not play_sound(cache_file, config.tts['output_format']):
                logging.error(f"Failed to play announcement for {button_id}")
                set_announcement_playing(False)
        except Exception as e:
            logging.error(f"Error processing button press: {e}")
            set_announcement_playing(False)  # Make sure to release lock in case of any error

def make_callback(button_id: str, config: Config):
    def callback(channel):
        # Enable this to debug the GPIO button presses
        logging.debug(f"Button {button_id} GPIO event detected")
        
        # Check if an announcement is playing BEFORE calling handle_button_press
        # This prevents button presses from being queued when an announcement is playing
        if not is_announcement_playing():
            # Don't block in the callback - it can cause GPIO issues
            # Instead, start a separate thread to handle the button press
            threading.Thread(target=handle_button_press, args=(button_id, config), daemon=True).start()
        else:
            logging.info(f"Button {button_id} press ignored at callback level - announcement already playing")
    return callback

def setup_gpio(config: Config):
    # Clean up any previous GPIO configuration
    try:
        GPIO.cleanup()
    except Exception as e:
        logging.debug(f"Initial GPIO cleanup error (ignored): {e}")

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
            # Use a longer hardware debounce time (500ms) to help avoid double-triggers
            GPIO.add_event_detect(pin, GPIO.FALLING, callback=callback, bouncetime=500)
        except RuntimeError as e:
            logging.error(f"Failed to add edge detection for pin {pin}: {e}")
    logging.info("GPIO setup complete; waiting for button presses...")

def pre_generate_announcements(config: Config):
    """Pre-generate all announcement files."""
    logging.info("Pre-generating announcement files...")
    
    for button_id, text in config.announcements.items():
        if text:
            logging.info(f"Pre-generating announcement for {button_id}...")
            cache_file = get_cache_filename(text, config.tts['voice_id'])
            
            # Check if cache file exists and is valid
            if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
                logging.info(f"Speech file for {button_id} already exists")
                continue
                
            # Need to generate a new file
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
    """Clean up resources when script exits."""
    # Make sure lock file is removed
    set_announcement_playing(False)
    GPIO.cleanup()

def main():
    # Clean up any stale lock files
    set_announcement_playing(False)
    
    config_path = "config.ini"
    last_checked_time = 0
    config = None
    
    try:
        config = load_config(config_path)
        # Generate all announcements on startup
        pre_generate_announcements(config)
    except Exception as e:
        logging.error("Failed to load config. Exiting.")
        sys.exit(1)
        
    setup_gpio(config)
    
    try:
        while True:
            # Periodically check if config file has been modified
            current_time = time.time()
            if current_time - last_checked_time > 10:  # Check every 10 seconds
                last_checked_time = current_time
                
                if os.path.exists(config_path):
                    mod_time = os.path.getmtime(config_path)
                    if mod_time > config.last_modified:
                        logging.info("Config file changed, reloading...")
                        new_config = load_config(config_path)
                        
                        # Check if announcements or voice changed
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
                        setup_gpio(config)  # Reconfigure GPIO in case pins changed
                        
                        if regenerate:
                            # Regenerate announcements if needed
                            pre_generate_announcements(config)
            
            # Using a small sleep to reduce CPU usage while waiting
            time.sleep(0.1)
    except KeyboardInterrupt:
        logging.info("Shutdown requested. Cleaning up GPIO.")
    finally:
        cleanup()

if __name__ == "__main__":
    main()
