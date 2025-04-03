#!/usr/bin/env python3
"""
kartrules.py

Background service for the Go-Kart Announcement System.
Monitors GPIO buttons on a Raspberry Pi, triggers Text-to-Speech (TTS)
generation using edge-tts, caches the audio, and plays announcements
using mpg123. It reloads configuration automatically from config.ini.

Author: Seth Morrow
"""

# Standard library imports
import asyncio
import logging
import sys
import os
import subprocess
import tempfile
import time
import hashlib
import threading
from pathlib import Path

# Import GPIO library for Raspberry Pi interaction
# This script will fail if not run on a Raspberry Pi or if the library isn't installed.
try:
    import RPi.GPIO as GPIO
except ImportError:
    # Log error and exit if GPIO library is not found
    logging.error("RPi.GPIO module not found. This script must be run on a Raspberry Pi with the library installed.")
    sys.exit(1)
except RuntimeError:
    # Handle cases where the library exists but cannot be loaded (e.g., wrong architecture)
     logging.error("Error importing RPi.GPIO. Ensure you are running on a Raspberry Pi and the library is correctly installed.")
     sys.exit(1)


# Third-party library imports
import edge_tts # Microsoft Edge Text-to-Speech library

# --- Configuration ---

# Configure logging to write to a file and print to console/journal
logging.basicConfig(
    level=logging.INFO,  # Set logging level (e.g., DEBUG for more detail)
    format="%(asctime)s [%(levelname)s] %(message)s",  # Log message format
    datefmt="%Y-%m-%d %H:%M:%S",  # Timestamp format
    handlers=[
        logging.FileHandler("announcement_script.log"),  # Log to this file
        logging.StreamHandler(sys.stdout) # Log to standard output (visible in journalctl for services)
    ]
)

# Shared state file path (must match the one used in settings.py)
# This file acts as a mutex (lock) to prevent multiple announcements
# (from buttons or web UI) playing simultaneously.
ANNOUNCEMENT_LOCK_FILE = "/tmp/announcement_playing.lock"

# Directory for storing cached TTS audio files
CACHE_DIR = "/tmp/tts_cache"
os.makedirs(CACHE_DIR, exist_ok=True)  # Ensure the cache directory exists

# Global dictionary to track the last time each button was pressed.
# Used for software debouncing to prevent rapid triggers from a single press.
# Keys are button IDs (e.g., "button1"), values are timestamps.
last_button_press = {
    "button1": 0,
    "button2": 0,
    "button3": 0
}

# Minimum time (in seconds) required between consecutive presses *of the same button*.
DEBOUNCE_TIMEOUT = 8.0

# Threading lock to prevent race conditions when multiple buttons
# might be pressed almost simultaneously or during config reloads.
# Ensures only one button handler logic runs at any given moment.
button_handler_lock = threading.Lock()

# --- Configuration Class ---

class Config:
    """Simple class to hold configuration loaded from config.ini."""
    def __init__(self):
        """Initialize default configuration values."""
        # Dictionary to store announcement text for each button ID
        self.announcements = {
            "button1": "Default announcement for Button 1.", # Default text
            "button2": "Default announcement for Button 2.",
            "button3": "Default announcement for Button 3."
        }
        # Dictionary for Text-to-Speech settings
        self.tts = {
            "voice_id": "en-US-AndrewMultilingualNeural", # Default voice
            "output_format": "mp3"  # Default audio format
        }
        # Dictionary for GPIO pin assignments (using BCM numbering scheme)
        self.gpio = {
            "button1": 17, # Default pin for button 1
            "button2": 27, # Default pin for button 2
            "button3": 22  # Default pin for button 3
        }
        # Store the last modification time of the config file to detect changes
        self.last_modified = 0

# --- State and Utility Functions ---

def is_announcement_playing():
    """
    Check if an announcement is currently playing by checking for the lock file.

    Returns:
        bool: True if the lock file exists, False otherwise.
    """
    return os.path.exists(ANNOUNCEMENT_LOCK_FILE)

def set_announcement_playing(is_playing=True):
    """
    Set the announcement playing state by creating or deleting the lock file.

    Args:
        is_playing (bool): True to create the lock file, False to delete it.
    """
    if is_playing:
        # Create the lock file
        try:
            with open(ANNOUNCEMENT_LOCK_FILE, 'w') as f:
                f.write(str(time.time())) # Write timestamp for debugging
            logging.debug(f"Created lock file: {ANNOUNCEMENT_LOCK_FILE}")
        except OSError as e:
            logging.error(f"Failed to create lock file {ANNOUNCEMENT_LOCK_FILE}: {e}")
    else:
        # Remove the lock file if it exists
        if os.path.exists(ANNOUNCEMENT_LOCK_FILE):
            try:
                os.remove(ANNOUNCEMENT_LOCK_FILE)
                logging.debug(f"Removed lock file: {ANNOUNCEMENT_LOCK_FILE}")
            except OSError as e:
                # Log warning as it might be removed by the settings script
                logging.warning(f"Failed to remove lock file {ANNOUNCEMENT_LOCK_FILE}: {e}")


def get_cache_filename(text: str, voice_id: str) -> str:
    """
    Generate a unique and deterministic cache filename based on the announcement
    text and the selected voice ID using an MD5 hash.

    Args:
        text (str): The announcement text.
        voice_id (str): The TTS voice ID (e.g., 'en-US-AndrewMultilingualNeural').

    Returns:
        str: The full path to the cache file (e.g., /tmp/tts_cache/hash.mp3).
    """
    # Combine text and voice ID
    identifier = text + voice_id
    # Create MD5 hash
    hash_object = hashlib.md5(identifier.encode())
    hash_str = hash_object.hexdigest()
    # Construct full path (assuming MP3 format based on default config)
    return os.path.join(CACHE_DIR, f"{hash_str}.mp3")

def load_config(config_path: str = "config.ini") -> Config | None:
    """
    Load configuration from the specified .ini file path.

    Args:
        config_path (str): The path to the configuration file.

    Returns:
        Config | None: A Config object populated with settings, or None if loading fails critically.
    """
    config = Config() # Initialize with defaults
    current_section = None
    logging.info(f"Attempting to load configuration from: {config_path}")

    try:
        # Check if file exists
        if not os.path.exists(config_path):
            logging.error(f"Config file not found: {config_path}. Using default values.")
            # Return default config if file doesn't exist, but don't crash
            return config # Or perhaps return None if defaults are not acceptable

        # Get the file's last modification time for change detection
        config.last_modified = os.path.getmtime(config_path)

        with open(config_path, 'r') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue
                # Section header (e.g., [tts])
                if line.startswith('[') and line.endswith(']'):
                    current_section = line[1:-1].lower()
                    if current_section not in ['announcements', 'tts', 'gpio']:
                        logging.warning(f"Ignoring unknown section '{current_section}' in {config_path} at line {line_num}")
                        current_section = None # Ignore keys under this unknown section
                    continue
                # Key-value pair (e.g., button1 = Hello)
                if '=' in line and current_section:
                    key, value = [x.strip() for x in line.split('=', 1)]
                    key = key.lower()
                    value = value.strip('"\'') # Remove potential quotes

                    # Populate the config object based on section and key
                    if current_section == 'announcements':
                        if key in config.announcements:
                            config.announcements[key] = value
                        else:
                            logging.warning(f"Ignoring unknown key '{key}' in section 'announcements' at line {line_num}")
                    elif current_section == 'tts':
                        if key in config.tts:
                            config.tts[key] = value.lower() if key == 'output_format' else value
                        else:
                             logging.warning(f"Ignoring unknown key '{key}' in section 'tts' at line {line_num}")
                    elif current_section == 'gpio':
                        if key in config.gpio:
                            try:
                                config.gpio[key] = int(value) # GPIO pins must be integers
                            except ValueError:
                                logging.warning(f"Invalid non-integer GPIO pin value for '{key}' ('{value}') at line {line_num}. Using default.")
                        else:
                             logging.warning(f"Ignoring unknown key '{key}' in section 'gpio' at line {line_num}")

        # Validate essential config values (e.g., voice_id)
        if not config.tts.get('voice_id'):
            logging.error("Configuration error: Missing required TTS 'voice_id' in [tts] section.")
            # Decide handling: raise error, return None, or use a hardcoded default?
            # Returning config but logging error allows partial functionality if other parts are ok.
            # return None # Stricter: fail if essential config is missing

        logging.info("Configuration loaded successfully.")
        return config
    except Exception as e:
        logging.exception(f"Critical error loading configuration from {config_path}: {e}") # Log full traceback
        return None # Return None on critical file read/parse errors

# --- TTS and Playback Functions ---

async def synthesize_speech_async(text: str, voice_id: str, output_path: str) -> bool:
    """
    Asynchronously generate speech audio from text using edge-tts.

    Args:
        text (str): The text to synthesize.
        voice_id (str): The voice ID to use.
        output_path (str): The file path to save the generated audio.

    Returns:
        bool: True if synthesis was successful, False otherwise.
    """
    try:
        logging.info(f"Synthesizing speech: '{text[:50]}...' (Voice: {voice_id}) -> {output_path}")
        communicate = edge_tts.Communicate(text, voice_id)
        await communicate.save(output_path) # Asynchronously save the audio
        # Verify creation and size
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logging.info("Speech synthesis successful.")
            return True
        else:
            logging.error("Speech synthesis failed - output file missing or empty.")
            # Attempt cleanup of potentially empty file
            if os.path.exists(output_path): os.remove(output_path)
            return False
    except edge_tts.NoAudioReceived:
         logging.error(f"Speech synthesis failed: No audio received from Edge TTS service for voice '{voice_id}'. Check voice ID and network.")
         return False
    except Exception as e:
        logging.error(f"Error during speech synthesis: {e}")
        return False

def play_sound(sound_path: str, output_format: str) -> bool:
    """
    Play an audio file using the 'mpg123' command-line player.
    This function blocks execution until playback is finished.
    It assumes the lock file is already set by the caller.

    Args:
        sound_path (str): Path to the audio file.
        output_format (str): The format of the audio file (used for logging).

    Returns:
        bool: True if playback was successful, False otherwise.
    """
    if not sound_path or not os.path.exists(sound_path):
        logging.error(f"Invalid sound path for playback: {sound_path}")
        return False
    try:
        logging.info(f"Playing sound file ({output_format}): {sound_path}")
        # Check if mpg123 command exists
        if subprocess.run(['which', 'mpg123'], capture_output=True).returncode != 0:
            logging.error("Playback failed: 'mpg123' command not found. Please install it (e.g., 'sudo apt install mpg123').")
            return False

        # Execute mpg123. '-q' for quiet mode. check=True raises error on failure.
        # This is a blocking call. The script waits here until mpg123 finishes.
        process = subprocess.run(['mpg123', '-q', sound_path], check=True, capture_output=True, text=True)
        logging.info("Sound playback completed successfully.")
        # logging.debug(f"mpg123 stderr: {process.stderr}") # Uncomment for debugging mpg123 output
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Error during sound playback (mpg123 failed): {e}. Stderr: {e.stderr}")
        return False
    except FileNotFoundError:
         logging.error("Playback failed: 'mpg123' command not found. Ensure it's installed and in PATH.")
         return False
    except Exception as e:
        logging.error(f"An unexpected error occurred during sound playback: {e}")
        return False
    # finally:
        # IMPORTANT: The lock file is now released in the *caller* (handle_button_press)
        # *after* this function returns. This ensures the lock is held for the
        # entire duration of the blocking playback.
        # set_announcement_playing(False) # Moved to caller's finally block


# --- Button Handling Logic ---

def handle_button_press(button_id: str, config: Config):
    """
    Core logic executed when a button press event is detected and validated.
    Handles debouncing, checks locks, synthesizes/caches audio, and plays sound.
    This function is intended to be run in a separate thread to avoid blocking
    the main GPIO event loop.

    Args:
        button_id (str): The identifier of the button pressed (e.g., "button1").
        config (Config): The currently loaded configuration object.
    """
    global last_button_press # Need to modify the global dictionary

    # Acquire the thread lock to ensure only one button handler runs at a time.
    # This prevents race conditions if buttons are pressed very close together.
    if not button_handler_lock.acquire(blocking=False):
        logging.info(f"Button {button_id} press ignored - another handler is already running.")
        return # Exit if another handler thread holds the lock

    try:
        # --- Pre-checks (inside the lock) ---
        current_time = time.time()

        # 1. Check Debounce: Has enough time passed since the *last* press of *this specific button*?
        if current_time - last_button_press.get(button_id, 0) < DEBOUNCE_TIMEOUT:
            logging.info(f"Button {button_id} press ignored - debounce timeout active.")
            return # Exit if debounce time hasn't passed for this button

        # 2. Check Global Lock: Is another announcement (from any source) already playing?
        # This check is crucial to prevent interrupting an ongoing announcement.
        if is_announcement_playing():
            logging.info(f"Button {button_id} press ignored - global announcement lock is active.")
            return # Exit if lock file exists

        # --- Passed Checks - Process the Announcement ---
        logging.info(f"Button {button_id} press detected and validated.")

        # CRITICAL: Set the global lock file *before* starting TTS or playback.
        set_announcement_playing(True)

        # Update the last press time for *this* button *after* passing checks and setting lock.
        last_button_press[button_id] = current_time

        # Retrieve announcement text and TTS settings from config
        announcement_text = config.announcements.get(button_id)
        voice_id = config.tts.get('voice_id')
        output_format = config.tts.get('output_format', 'mp3')

        if not announcement_text:
            logging.error(f"No announcement text configured for button '{button_id}'.")
            set_announcement_playing(False) # Release lock if no text
            return
        if not voice_id:
             logging.error(f"No TTS voice_id configured.")
             set_announcement_playing(False) # Release lock if no voice
             return

        # --- Synthesize or Get from Cache ---
        cache_file = get_cache_filename(announcement_text, voice_id)

        # Check if cache file exists and is valid
        if not os.path.exists(cache_file) or os.path.getsize(cache_file) == 0:
            logging.info(f"Cache miss for button {button_id} - generating new speech file.")
            # Run the async synthesis function synchronously here
            # Note: This blocks the handler thread during synthesis.
            synthesis_success = asyncio.run(synthesize_speech_async(announcement_text, voice_id, cache_file))
            if not synthesis_success:
                logging.error(f"Failed to synthesize speech for button {button_id}.")
                # Attempt cleanup of potentially empty file
                if os.path.exists(cache_file): os.remove(cache_file)
                set_announcement_playing(False) # Release lock on synthesis failure
                return # Exit if synthesis failed
        else:
            logging.info(f"Using cached speech file for button {button_id}: {cache_file}")

        # --- Play the Sound ---
        logging.info(f"Playing announcement for button {button_id}...")
        playback_success = play_sound(cache_file, output_format) # This blocks until done

        if not playback_success:
            logging.error(f"Failed to play announcement sound for button {button_id}.")
            # Lock is released in the finally block below

    except Exception as e:
        logging.exception(f"Error during button press handling for {button_id}: {e}")
        # Ensure lock is released even if unexpected errors occur
        set_announcement_playing(False)
    finally:
        # CRITICAL: Release the global lock *after* playback finishes or if an error occurred.
        # This happens regardless of whether playback was successful.
        set_announcement_playing(False)
        # Release the thread lock so other button presses can be handled.
        button_handler_lock.release()
        logging.debug(f"Button handler for {button_id} finished.")


def make_callback(button_id: str, config: Config):
    """
    Factory function to create a unique callback function for each GPIO button.
    The returned callback will execute `handle_button_press` in a separate thread.

    Args:
        button_id (str): The identifier for the button (e.g., "button1").
        config (Config): The current configuration object.

    Returns:
        function: A callback function suitable for `GPIO.add_event_detect`.
    """
    def callback(channel):
        """
        The actual callback function triggered by GPIO event detection.
        It logs the event and starts the main handler in a non-blocking thread.
        """
        logging.debug(f"GPIO event detected on channel {channel} (Button {button_id})")

        # OPTIONAL: Add an immediate check for the global lock here.
        # This can prevent even starting the thread if an announcement is playing.
        # if is_announcement_playing():
        #     logging.debug(f"GPIO event for {button_id} ignored at callback level - lock active.")
        #     return

        # Start the main button handling logic in a separate thread.
        # This prevents the GPIO callback (which runs in a special context)
        # from blocking, which is crucial for reliable GPIO event handling.
        # 'daemon=True' ensures threads don't prevent the main script from exiting.
        handler_thread = threading.Thread(
            target=handle_button_press,
            args=(button_id, config),
            daemon=True
        )
        handler_thread.start()

    return callback

# --- GPIO Setup ---

def setup_gpio(config: Config):
    """
    Configure Raspberry Pi GPIO pins based on the loaded configuration.
    Sets up input pins with pull-up resistors and adds event detection.

    Args:
        config (Config): The Config object containing GPIO pin assignments.
    """
    logging.info("Setting up GPIO pins...")
    try:
        # Attempt to clean up any previous GPIO state before setting new mode.
        # This can prevent warnings if the script was run before uncleanly.
        try:
            GPIO.cleanup()
            logging.debug("Performed initial GPIO cleanup.")
        except Exception as e:
            # Ignore potential errors during initial cleanup (e.g., library not yet initialized)
             logging.debug(f"Ignoring error during initial GPIO cleanup (may be expected): {e}")

        # Set GPIO numbering scheme to BCM (Broadcom SOC channel)
        # Alternative is GPIO.BOARD (physical pin numbers)
        GPIO.setmode(GPIO.BCM)
        # Disable common GPIO warnings (e.g., "channel already in use")
        GPIO.setwarnings(False)

        # Iterate through the button configurations in the gpio section
        for button_key, pin in config.gpio.items():
            if not isinstance(pin, int) or pin <= 0:
                 logging.warning(f"Skipping invalid GPIO pin configuration for {button_key}: {pin}")
                 continue

            logging.info(f"Setting up GPIO pin {pin} for {button_key}...")
            try:
                # Configure the pin as an input pin.
                # `pull_up_down=GPIO.PUD_UP` enables the internal pull-up resistor.
                # This means the pin reads HIGH by default and goes LOW when the button
                # connects it to GND.
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

                # Remove any existing event detection on the pin first (robustness)
                try:
                     GPIO.remove_event_detect(pin)
                     logging.debug(f"Removed existing event detection for pin {pin}.")
                except Exception:
                     logging.debug(f"No existing event detection to remove for pin {pin}.") # Expected usually

                # Create the callback function for this specific button
                callback_func = make_callback(button_key, config)

                # Add event detection for a falling edge (HIGH to LOW transition).
                # This triggers when the button is pressed (connecting pin to GND).
                # `bouncetime=500` adds hardware/software debounce (milliseconds)
                # to help ignore noise or rapid bouncing when the button contacts close.
                # Adjust bouncetime if needed based on button quality.
                GPIO.add_event_detect(pin, GPIO.FALLING, callback=callback_func, bouncetime=500)
                logging.info(f"Added FALLING edge detection for pin {pin} ({button_key})")

            except Exception as e:
                # Catch errors during setup/event detection for a specific pin
                logging.error(f"Failed to set up GPIO or add event detection for pin {pin} ({button_key}): {e}")
                # Continue trying to set up other pins

        logging.info("GPIO setup complete. Waiting for button presses...")

    except Exception as e:
        # Catch broader errors during GPIO setup (e.g., setmode failure)
        logging.exception(f"Fatal error during GPIO setup: {e}")
        # Consider exiting if GPIO setup fails critically
        # sys.exit(1)

# --- Pre-generation and Main Loop ---

def pre_generate_all_announcements(config: Config):
    """
    Generate and cache audio files for all announcements defined in the config.
    This runs synchronously.

    Args:
        config (Config): The current configuration object.
    """
    logging.info("Starting pre-generation of all announcement files...")
    voice_id = config.tts.get('voice_id')
    if not voice_id:
        logging.error("Cannot pre-generate: TTS voice_id not configured.")
        return

    generated_count = 0
    failed_count = 0
    # Iterate through configured announcements
    for button_id, text in config.announcements.items():
        if text: # Only generate if text is defined
            logging.debug(f"Pre-generating announcement for {button_id}...")
            cache_file = get_cache_filename(text, voice_id)
            # Check cache first
            if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
                logging.info(f"Speech file for {button_id} already exists in cache.")
                generated_count += 1 # Count cached as successful pre-generation
                continue

            # Generate if not cached or empty
            logging.info(f"Generating new speech file for {button_id}")
            # Run async function synchronously
            success = asyncio.run(synthesize_speech_async(text, voice_id, cache_file))
            if success:
                logging.info(f"Successfully generated speech file for {button_id}")
                generated_count += 1
            else:
                logging.error(f"Failed to generate speech file for {button_id}")
                failed_count += 1
                # Attempt cleanup of potentially failed file
                if os.path.exists(cache_file): os.remove(cache_file)
        else:
             logging.debug(f"Skipping pre-generation for {button_id} (no text defined).")

    logging.info(f"Finished pre-generating announcements. Success/Cached: {generated_count}, Failed: {failed_count}")

def cleanup():
    """Function to clean up resources when the script exits."""
    logging.info("Cleaning up resources...")
    # Ensure the lock file is removed on exit
    set_announcement_playing(False)
    # Release GPIO resources
    try:
        GPIO.cleanup()
        logging.info("GPIO cleanup performed.")
    except Exception as e:
         logging.error(f"Error during GPIO cleanup: {e}")


def main():
    """Main execution function."""
    logging.info("Starting Go-Kart Rules Button Monitor Service...")
    # Register the cleanup function to be called on script exit
    atexit.register(cleanup)

    # Perform initial cleanup of any potentially stale lock file
    set_announcement_playing(False)

    config_path = "config.ini"
    last_config_check_time = 0
    config = None

    # --- Initial Configuration Load ---
    try:
        config = load_config(config_path)
        if config is None:
            logging.critical("Failed to load initial configuration. Exiting.")
            sys.exit(1) # Exit if initial load fails critically
        # Pre-generate announcements based on initial config
        pre_generate_all_announcements(config)
    except Exception as e:
        logging.exception("An unexpected error occurred during initial setup. Exiting.")
        sys.exit(1)

    # --- Initial GPIO Setup ---
    setup_gpio(config) # Set up GPIO based on the loaded config

    # --- Main Loop ---
    logging.info("Entering main loop (monitoring config file changes)...")
    try:
        while True:
            current_time = time.time()
            # Periodically check if the configuration file has been modified
            if current_time - last_config_check_time > 10: # Check every 10 seconds
                last_config_check_time = current_time
                try:
                    if os.path.exists(config_path):
                        mod_time = os.path.getmtime(config_path)
                        # Compare modification time with the last loaded time
                        if mod_time > config.last_modified:
                            logging.info("Config file change detected, reloading...")
                            # Acquire lock to prevent button handling during reload
                            with button_handler_lock:
                                logging.info("Acquired handler lock for config reload.")
                                new_config = load_config(config_path)
                                if new_config:
                                    # --- Check if critical settings changed ---
                                    regenerate = False
                                    if new_config.tts['voice_id'] != config.tts['voice_id']:
                                        logging.info("TTS voice_id changed.")
                                        regenerate = True
                                    if new_config.announcements != config.announcements:
                                         logging.info("Announcement text changed.")
                                         regenerate = True
                                    gpio_changed = new_config.gpio != config.gpio
                                    if gpio_changed:
                                         logging.info("GPIO pin configuration changed.")

                                    # Update the current config
                                    config = new_config

                                    # Re-setup GPIO if pins changed
                                    if gpio_changed:
                                        logging.info("Re-setting up GPIO due to config changes.")
                                        setup_gpio(config)

                                    # Regenerate announcements if text or voice changed
                                    if regenerate:
                                        logging.info("Regenerating announcements due to config changes.")
                                        pre_generate_all_announcements(config)
                                else:
                                     logging.error("Failed to reload configuration after detecting changes.")
                                logging.info("Released handler lock after config reload attempt.")
                        else:
                             logging.debug("Config file modification time unchanged.")
                    else:
                        logging.warning(f"Config file '{config_path}' not found during periodic check.")

                except Exception as e:
                    logging.exception(f"Error during periodic config check: {e}")

            # Keep the main thread alive. time.sleep() reduces CPU usage.
            # Event detection happens in background threads managed by RPi.GPIO.
            time.sleep(0.5) # Sleep for a short duration

    except KeyboardInterrupt:
        logging.info("Shutdown signal (KeyboardInterrupt) received. Exiting gracefully.")
    except Exception as e:
         logging.exception(f"An unexpected error occurred in the main loop: {e}")
    finally:
        # Cleanup is automatically called via atexit registration
        logging.info("Go-Kart Rules Button Monitor Service stopped.")


if __name__ == "__main__":
    # This block executes only when the script is run directly
    main()
