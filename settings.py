#!/usr/bin/env python3
"""
settings.py

Flask web application to configure and test the Go-Kart Announcement System.
Provides a web interface for managing announcement texts, TTS settings,
testing playback, and monitoring the system. Works in conjunction with
kartrules.py.

Author: Seth Morrow 
"""

# Standard library imports
import os
import logging
import asyncio
import tempfile
import subprocess
import time
import datetime
import threading
import atexit
import hashlib
from pathlib import Path

# Third-party library imports
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, send_file)
import edge_tts  # Microsoft Edge Text-to-Speech library

# --- Configuration ---

# Configure logging to write to a file and print to console
logging.basicConfig(
    level=logging.INFO,  # Set logging level (INFO, DEBUG, WARNING, ERROR, CRITICAL)
    format="%(asctime)s [%(levelname)s] %(message)s",  # Log message format
    datefmt="%Y-%m-%d %H:%M:%S",  # Timestamp format
    handlers=[
        logging.FileHandler("announcement_script.log"),  # Log to file
        logging.StreamHandler()  # Log to console/stderr
    ]
)

# Shared state file path (must match the one used in kartrules.py)
# This file indicates if an announcement is currently playing.
ANNOUNCEMENT_LOCK_FILE = "/tmp/announcement_playing.lock"

# Directory for storing cached TTS audio files
# Using a cache avoids regenerating the same audio repeatedly.
CACHE_DIR = "/tmp/tts_cache"
os.makedirs(CACHE_DIR, exist_ok=True)  # Ensure the cache directory exists

# Global variables for announcement control within this web app
last_announcement_time = 0  # Timestamp of the last announcement played via web UI
ANNOUNCEMENT_COOLDOWN = 8.0  # Minimum seconds to wait between playing announcements via web UI

# Initialize Flask web application
app = Flask(__name__,
            static_folder='static',    # Folder for static files (CSS, JS, images)
            template_folder='templates') # Folder for HTML templates
# Secret key for session management (important for security, change in production)
app.secret_key = 'your_secret_key_here' # TODO: Change this to a strong, unique secret key

# Record the application startup time for uptime calculation
start_time = time.time()

# --- Utility Functions ---

def is_announcement_playing():
    """
    Check if an announcement is currently playing by checking for the lock file.
    This lock file can be created by either kartrules.py or this settings app.

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
        # Create the lock file to signal an announcement is playing
        try:
            with open(ANNOUNCEMENT_LOCK_FILE, 'w') as f:
                f.write(str(time.time())) # Write timestamp for debugging
            logging.debug(f"Created lock file: {ANNOUNCEMENT_LOCK_FILE}")
        except OSError as e:
            logging.error(f"Failed to create lock file {ANNOUNCEMENT_LOCK_FILE}: {e}")
    else:
        # Remove the lock file if it exists to signal announcement finished
        if os.path.exists(ANNOUNCEMENT_LOCK_FILE):
            try:
                os.remove(ANNOUNCEMENT_LOCK_FILE)
                logging.debug(f"Removed lock file: {ANNOUNCEMENT_LOCK_FILE}")
            except OSError as e:
                # Log warning instead of error as it might be removed by the other script
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
    # Combine text and voice ID to create a unique identifier
    identifier = text + voice_id
    # Create an MD5 hash of the identifier (ensures filename is safe and unique)
    hash_object = hashlib.md5(identifier.encode())
    hash_str = hash_object.hexdigest()
    # Construct the full path within the cache directory
    return os.path.join(CACHE_DIR, f"{hash_str}.mp3") # Assumes MP3 format for now

class ConfigHandler:
    """Handles reading from and writing to the config.ini file."""
    def __init__(self, config_file: str = "config.ini"):
        """
        Initialize the ConfigHandler.

        Args:
            config_file (str): Path to the configuration file. Defaults to "config.ini".
        """
        self.config_file = config_file
        # Default configuration structure
        self.config = {
            'announcements': {
                'button1': '',
                'button2': '',
                'button3': ''
            },
            'tts': {
                'voice_id': 'en-US-AndrewMultilingualNeural', # Default voice
                'output_format': 'mp3'
            },
            'gpio': { # Default GPIO pins (BCM numbering)
                'button1': 17,
                'button2': 27,
                'button3': 22
            }
        }

    def read_config(self):
        """
        Read configuration settings from the .ini file.
        Populates self.config with values found in the file.

        Returns:
            dict: The configuration dictionary.
        """
        try:
            if os.path.exists(self.config_file):
                logging.info(f"Reading configuration from: {self.config_file}")
                with open(self.config_file, 'r') as f:
                    current_section = None
                    for line in f:
                        line = line.strip()
                        # Skip empty lines and comments
                        if not line or line.startswith('#'):
                            continue
                        # Identify section headers (e.g., [announcements])
                        if line.startswith('[') and line.endswith(']'):
                            current_section = line[1:-1].lower()
                            continue
                        # Identify key-value pairs (e.g., button1 = Hello)
                        if '=' in line and current_section:
                            key, value = [x.strip() for x in line.split('=', 1)]
                            # Remove potential quotes around the value
                            value = value.strip('"\'')
                            # Store value in the appropriate section and key
                            if current_section in self.config and key.lower() in self.config[current_section]:
                                if current_section == 'gpio':
                                    try:
                                        # Convert GPIO pins to integers
                                        self.config[current_section][key.lower()] = int(value)
                                    except ValueError:
                                        logging.warning(f"Invalid non-integer GPIO pin value for {key}: {value}. Using default.")
                                else:
                                    self.config[current_section][key.lower()] = value
                            else:
                                logging.warning(f"Ignoring unknown config key '{key}' in section '{current_section}'")
            else:
                 logging.warning(f"Config file '{self.config_file}' not found. Using default values.")
            return self.config
        except Exception as e:
            logging.error(f"Error reading config file '{self.config_file}': {e}")
            # Return default config in case of error
            return self.config

    def write_config(self):
        """
        Write the current configuration (self.config) back to the .ini file.
        Overwrites the existing file.
        """
        try:
            logging.info(f"Writing configuration to: {self.config_file}")
            with open(self.config_file, 'w') as f:
                # Write announcements section
                f.write("[announcements]\n")
                for key, value in self.config['announcements'].items():
                    # Ensure values are properly quoted if they contain special characters (optional but good practice)
                    # f.write(f"{key} = \"{value}\"\n") # Example with quotes
                    f.write(f"{key} = {value}\n")
                # Write TTS section
                f.write("\n[tts]\n")
                for key, value in self.config['tts'].items():
                    f.write(f"{key} = {value}\n")
                # Write GPIO section
                f.write("\n[gpio]\n")
                for key, value in self.config['gpio'].items():
                    f.write(f"{key} = {value}\n")
            logging.info("Configuration saved successfully.")
        except Exception as e:
            logging.error(f"Error writing config file '{self.config_file}': {e}")
            raise # Re-raise the exception so the calling function knows about the failure

async def synthesize_speech_async(text: str, voice_id: str, output_path: str) -> bool:
    """
    Asynchronously generate speech audio from text using edge-tts.

    Args:
        text (str): The text to synthesize.
        voice_id (str): The voice ID to use (e.g., 'en-US-AndrewMultilingualNeural').
        output_path (str): The file path to save the generated audio.

    Returns:
        bool: True if synthesis was successful, False otherwise.
    """
    try:
        logging.info(f"Synthesizing speech for: '{text[:50]}...' using voice '{voice_id}'")
        # Create a Communicate instance with the text and voice
        communicate = edge_tts.Communicate(text, voice_id)
        # Asynchronously save the audio to the specified file
        await communicate.save(output_path)
        # Verify that the file was created and is not empty
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logging.info(f"Speech synthesis successful. Saved to: {output_path}")
            return True
        else:
            logging.error(f"Speech synthesis failed - output file missing or empty: {output_path}")
            return False
    except edge_tts.NoAudioReceived:
         logging.error(f"Speech synthesis failed: No audio received from Edge TTS service for voice '{voice_id}'. Check voice ID and network.")
         return False
    except Exception as e:
        logging.error(f"Error during speech synthesis: {e}")
        return False

def play_sound(sound_path: str, output_format: str = 'mp3') -> bool:
    """
    Play an audio file using the 'mpg123' command-line player.
    This function blocks until playback is complete.

    Args:
        sound_path (str): Path to the audio file (e.g., an MP3 file).
        output_format (str): The format of the audio file (currently only used for logging).

    Returns:
        bool: True if playback was successful, False otherwise.
    """
    if not sound_path or not os.path.exists(sound_path):
        logging.error(f"Invalid sound path for playback: {sound_path}")
        return False
    try:
        logging.info(f"Attempting to play sound file ({output_format}): {sound_path}")
        # Check if mpg123 is installed and available in PATH
        if subprocess.run(['which', 'mpg123'], capture_output=True, text=True).returncode != 0:
            logging.error("Playback failed: 'mpg123' command not found. Please install mpg123 (e.g., 'sudo apt install mpg123').")
            return False

        # Execute mpg123 to play the sound file.
        # '-q' makes it quiet (suppresses diagnostic messages).
        # `check=True` raises CalledProcessError if mpg123 returns a non-zero exit code.
        process = subprocess.run(['mpg123', '-q', sound_path], check=True, capture_output=True, text=True)
        logging.info(f"Sound played successfully: {sound_path}")
        # Optional: Log mpg123 output/errors if needed (currently captured but not logged)
        # logging.debug(f"mpg123 stdout: {process.stdout}")
        # logging.debug(f"mpg123 stderr: {process.stderr}")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Error playing sound with mpg123: {e}. stderr: {e.stderr}")
        return False
    except FileNotFoundError:
        logging.error("Playback failed: 'mpg123' command not found. Please ensure it is installed and in the system's PATH.")
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred during sound playback: {e}")
        return False
    # finally:
        # NOTE: The lock file is now cleared in the calling function (/play_instant)
        # AFTER this function returns, ensuring the lock persists for the duration
        # of the synchronous playback.
        # set_announcement_playing(False) # Moved to caller

def get_system_uptime() -> str:
    """
    Calculate the uptime of this web application instance.

    Returns:
        str: A human-readable string representing the uptime (e.g., "1 day, 2:30:05").
    """
    uptime_seconds = time.time() - start_time
    # Format timedelta object into a readable string
    return str(datetime.timedelta(seconds=int(uptime_seconds)))

def pre_generate_announcement(text: str, voice_id: str) -> str | None:
    """
    Generate and cache a single announcement audio file if it doesn't already exist.
    This is called synchronously.

    Args:
        text (str): The announcement text.
        voice_id (str): The TTS voice ID.

    Returns:
        str | None: The path to the cached file if successful, None otherwise.
    """
    if not text or not voice_id:
        logging.warning("Skipping pre-generation: Empty text or voice_id.")
        return None

    try:
        # Determine the expected cache file path
        cache_file = get_cache_filename(text, voice_id)

        # Check if cache file exists and is valid (not empty)
        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
            logging.info(f"Announcement already cached: {cache_file}")
            return cache_file

        # File needs to be generated
        logging.info(f"Generating new speech cache file for: '{text[:50]}...'")

        # Run the asynchronous synthesis function synchronously for pre-generation
        # This blocks until the synthesis is complete.
        success = asyncio.run(synthesize_speech_async(text, voice_id, cache_file))

        if success:
            logging.info(f"Successfully generated cached file: {cache_file}")
            return cache_file
        else:
            # Attempt to clean up potentially empty/corrupt file if generation failed
            if os.path.exists(cache_file):
                try:
                    os.remove(cache_file)
                except OSError as e:
                    logging.warning(f"Could not remove failed cache file {cache_file}: {e}")
            logging.error("Failed to generate speech cache file.")
            return None
    except Exception as e:
        logging.error(f"Error during single announcement pre-generation: {e}")
        return None

def pre_generate_all_announcements():
    """
    Pre-generate audio files for all announcements defined in the config file.
    This runs synchronously and is typically called on startup or after config changes.
    """
    logging.info("Starting pre-generation of all configured announcement files...")
    config_handler = ConfigHandler()
    config = config_handler.read_config()
    voice_id = config['tts'].get('voice_id') # Use .get() for safety

    if not voice_id:
        logging.error("Cannot pre-generate announcements: TTS voice_id not found in configuration.")
        return

    announcements_generated = 0
    announcements_failed = 0
    # Iterate through the configured announcements
    for button_id, text in config['announcements'].items():
        if text: # Only process if text is defined
            logging.info(f"Pre-generating for {button_id}...")
            # Call the single pre-generation function
            speech_file = pre_generate_announcement(text, voice_id)
            if speech_file:
                logging.info(f"Successfully generated/verified cache for {button_id}")
                announcements_generated += 1
            else:
                logging.error(f"Failed to generate cache file for {button_id}")
                announcements_failed += 1
        else:
            logging.info(f"Skipping pre-generation for {button_id} (no text defined).")

    logging.info(f"Finished pre-generating announcements. Success: {announcements_generated}, Failed: {announcements_failed}")

# --- Flask Routes ---

@app.route('/')
def index():
    """
    Render the main configuration page (config.html).
    Loads the current configuration and passes it to the template.
    """
    config_handler = ConfigHandler()
    config = config_handler.read_config()

    # Get current time for display in the template
    current_time = datetime.datetime.now()

    # Render the main HTML template with current configuration data
    return render_template(
        'config.html', # The HTML file to render
        # Pass configuration sections to the template context
        announcements=config['announcements'],
        tts=config['tts'],
        gpio=config['gpio'],
        # Pass other useful variables
        current_time=current_time,
        current_year=current_time.year,
        uptime=get_system_uptime()
    )

@app.route('/save_config', methods=['POST'])
def save_config():
    """
    Handle POST requests to save the configuration changes made in the web form.
    Updates the config.ini file and triggers announcement regeneration if needed.
    """
    try:
        config_handler = ConfigHandler()
        config = config_handler.read_config() # Read current config first

        # Store old values to check if regeneration is needed
        old_announcements = config['announcements'].copy()
        old_voice_id = config['tts'].get('voice_id')

        # Update config dictionary with data from the submitted form
        config['announcements']['button1'] = request.form.get('button1', '').strip()
        config['announcements']['button2'] = request.form.get('button2', '').strip()
        config['announcements']['button3'] = request.form.get('button3', '').strip()
        config['tts']['voice_id'] = request.form.get('voice_id', '').strip()
        config['tts']['output_format'] = request.form.get('output_format', 'mp3').strip()
        # Note: GPIO pins are read-only in this UI, so we don't update them here.

        # Save the updated configuration back to the file
        config_handler.config = config # Update the handler's internal config
        config_handler.write_config() # Write to config.ini

        # Determine if announcements need regeneration
        voice_changed = old_voice_id != config['tts']['voice_id']
        text_changed = any(config['announcements'][key] != old_announcements.get(key)
                           for key in config['announcements'])

        # Display appropriate success message
        if voice_changed:
            flash_msg = 'Configuration saved successfully! All announcements will be regenerated with the new voice.'
        elif text_changed:
             flash_msg = 'Configuration saved successfully! Changed announcements will be regenerated.'
        else:
             flash_msg = 'Configuration saved successfully! No announcement regeneration needed.'

        flash(flash_msg, 'success') # Show success message to the user

        # Trigger regeneration in a background thread if needed
        if voice_changed or text_changed:
             logging.info("Configuration changed, triggering background regeneration of announcements.")
             # Using threading to avoid blocking the web request
             regeneration_thread = threading.Thread(target=pre_generate_all_announcements, daemon=True)
             regeneration_thread.start()

        # Redirect back to the main page
        return redirect(url_for('index'))

    except Exception as e:
        logging.error(f"Error saving configuration: {e}")
        flash(f'Error saving configuration: {str(e)}', 'error') # Show error message
        return redirect(url_for('index')) # Redirect back even on error

@app.route('/play_instant', methods=['POST'])
def play_instant():
    """
    Handle POST requests to test-play an announcement immediately via the web UI.
    Checks lock file and cooldown before playing.
    """
    global last_announcement_time # Need to modify the global variable

    # --- Cooldown and Lock Check ---
    current_time = time.time()
    if is_announcement_playing():
        logging.info("Play request denied: Announcement lock file exists.")
        # 429 Too Many Requests is appropriate for rate limiting/cooldown
        return jsonify({'error': 'Another announcement is currently playing (check lock file).'}), 429

    time_since_last = current_time - last_announcement_time
    if time_since_last < ANNOUNCEMENT_COOLDOWN:
        wait_time = round(ANNOUNCEMENT_COOLDOWN - time_since_last, 1)
        logging.info(f"Play request denied: Cooldown active. Wait {wait_time}s.")
        return jsonify({'error': f'Please wait {wait_time} seconds before playing another announcement.'}), 429

    # --- Process Request ---
    try:
        data = request.get_json()
        if not data or 'text' not in data:
            logging.error("Play request failed: Missing 'text' in JSON payload.")
            return jsonify({'error': 'Missing required "text" parameter in request.'}), 400

        text_to_play = data['text'].strip()
        if not text_to_play:
            logging.error("Play request failed: Provided 'text' is empty.")
            return jsonify({'error': 'Announcement text cannot be empty.'}), 400

        # --- Set Lock and Update Timestamp ---
        # CRITICAL: Set lock *before* synthesis/playback and update time immediately
        set_announcement_playing(True)
        last_announcement_time = current_time # Update time *after* checks passed

        # --- Synthesize and Play ---
        # Get current voice settings from config
        config_handler = ConfigHandler()
        config = config_handler.read_config()
        voice_id = config['tts'].get('voice_id')
        output_format = config['tts'].get('output_format', 'mp3')

        if not voice_id:
             set_announcement_playing(False) # Release lock
             logging.error("Play request failed: TTS voice_id not configured.")
             return jsonify({'error': 'TTS Voice ID is not configured.'}), 500

        # Generate or retrieve cached audio file (synchronously for web request)
        cache_file = pre_generate_announcement(text_to_play, voice_id)

        if not cache_file:
            set_announcement_playing(False) # Release lock
            logging.error("Play request failed: Could not generate/retrieve audio cache file.")
            return jsonify({'error': 'Failed to synthesize speech or retrieve cache.'}), 500

        # Play the sound file (this blocks until done)
        playback_success = play_sound(cache_file, output_format)

        # --- Release Lock and Respond ---
        # IMPORTANT: Release lock only *after* play_sound finishes
        set_announcement_playing(False)

        if playback_success:
            logging.info("Instant announcement played successfully via web UI.")
            return jsonify({'success': True, 'message': 'Announcement played successfully'}), 200
        else:
            logging.error("Play request failed: Playback command failed.")
            # Don't necessarily need to release lock here as play_sound failure might indicate other issues
            # set_announcement_playing(False) # Already released above
            return jsonify({'error': 'Failed to execute playback command (check mpg123 installation and audio setup).'}), 500

    except Exception as e:
        logging.exception(f"Unexpected error in /play_instant: {e}") # Log full traceback
        set_announcement_playing(False) # Ensure lock is released on any exception
        return jsonify({'error': f'An internal server error occurred: {str(e)}'}), 500


@app.route('/announcement_status')
def announcement_status():
    """
    Provide the current status of announcement playback (checks lock file).
    Used by the frontend JavaScript to disable/enable test buttons.

    Returns:
        JSON: {'is_playing': bool, 'lock_file_exists': bool}
               'is_playing' is true if the lock file exists.
               'lock_file_exists' explicitly states if the file was found.
               (Note: Cooldown is handled client-side or in /play_instant now)
    """
    lock_exists = is_announcement_playing()
    return jsonify({
        'is_playing': lock_exists, # Simplified: main status depends only on lock file
        'lock_file_exists': lock_exists
        # Removed cooldown logic from here, handled in play_instant
    })

@app.route('/logs')
def get_logs():
    """
    Retrieve the last N lines of the log file (announcement_script.log).

    Returns:
        str: Plain text content of the log file (last 100 lines).
        Response: 404 if log file not found.
        Response: 500 on read error.
    """
    try:
        log_file = "announcement_script.log"
        lines_to_fetch = 100 # Number of recent log lines to display

        if not os.path.exists(log_file):
            logging.info("Log file requested but not found.")
            return "Log file not found.", 404

        # Read the last N lines efficiently (more robust than reading all)
        lines = []
        with open(log_file, 'rb') as f: # Open in binary to handle potential encoding issues
            # Seek close to the end, assuming ~100 bytes/line as a guess
            f.seek(0, os.SEEK_END)
            end_pos = f.tell()
            f.seek(max(0, end_pos - lines_to_fetch * 100), os.SEEK_SET)
            # Read lines from this point
            lines = [line.decode('utf-8', errors='replace') for line in f.readlines()]

        # Take the actual last N lines in case we read too many/few
        last_lines = lines[-lines_to_fetch:]
        log_content = ''.join(last_lines)

        # Return as plain text
        response = app.response_class(response=log_content, status=200, mimetype='text/plain')
        return response

    except Exception as e:
        logging.error(f"Error reading log file '{log_file}': {e}")
        return f"Error reading log file: {str(e)}", 500

@app.route('/download_logs')
def download_logs():
    """
    Allow the user to download the complete log file.

    Returns:
        File attachment: The announcement_script.log file.
        Redirect: Redirects to index with error flash if file not found or error occurs.
    """
    try:
        log_file_path = Path("announcement_script.log").resolve() # Get absolute path
        if not log_file_path.is_file():
            logging.error("Log download request failed: File not found.")
            flash('Log file (announcement_script.log) not found.', 'error')
            return redirect(url_for('index'))

        logging.info(f"Providing log file for download: {log_file_path}")
        # Send the file as an attachment
        return send_file(log_file_path, as_attachment=True, download_name='kart_announcement_logs.log')

    except Exception as e:
        logging.error(f"Error during log file download: {e}")
        flash(f'Error downloading logs: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/restart_service', methods=['POST'])
def restart_service():
    """
    **Placeholder** endpoint to simulate restarting the `kartrules.py` service.
    In a real setup, this should trigger `sudo systemctl restart kartrules.service`.
    Currently, it only logs the request, resets locks, and regenerates announcements.
    """
    try:
        # --- !! IMPORTANT !! ---
        # This is a placeholder. For a real restart, you need to execute
        # the system command. This requires careful permission handling
        # (e.g., running this web server as a user with specific sudo rights
        # ONLY for the systemctl restart command, which is complex and risky).
        # A safer approach is manual restart via SSH or a dedicated management tool.
        # Example command (needs permissions):
        # subprocess.run(['sudo', 'systemctl', 'restart', 'kartrules.service'], check=True)
        # -----------------------

        logging.warning("Simulating service restart via web UI request.")

        # Reset lock state just in case
        set_announcement_playing(False)
        logging.info("Lock file cleared during simulated restart.")

        # Trigger announcement regeneration in the background
        logging.info("Triggering background regeneration after simulated restart.")
        regeneration_thread = threading.Thread(target=pre_generate_all_announcements, daemon=True)
        regeneration_thread.start()

        # Simulate time taken for restart
        time.sleep(1) # Short delay to make it feel like something happened

        return jsonify({'success': True, 'message': 'Service restart simulated successfully. Announcements regenerating.'}), 200
    except Exception as e:
        logging.error(f"Error during simulated service restart: {e}")
        return jsonify({'error': f'Error during simulated restart: {str(e)}'}), 500

@app.route('/check_dependencies')
def check_dependencies():
    """
    Check if external command-line dependencies (like mpg123) are available.

    Returns:
        JSON: Dictionary indicating the status of checked dependencies.
               e.g., {'mpg123': True}
    """
    dependencies = {
        'mpg123': False,
        # Add other command-line tools here if needed
    }
    logging.info("Checking for dependency: mpg123")
    # Use 'which' command to check if mpg123 is in the system's PATH
    try:
        result = subprocess.run(['which', 'mpg123'], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            dependencies['mpg123'] = True
            logging.info("mpg123 found.")
        else:
             logging.warning("mpg123 not found in PATH.")
    except FileNotFoundError:
         logging.warning("'which' command not found, cannot check for mpg123.")
    except Exception as e:
        logging.error(f"Error checking for mpg123 dependency: {e}")

    return jsonify(dependencies)

@app.route('/reset_locks', methods=['POST'])
def reset_locks():
    """
    Emergency endpoint accessible via web UI (e.g., hidden button or specific URL)
    to forcefully remove the announcement lock file if it gets stuck. Use with caution.
    """
    try:
        logging.warning("Force lock reset requested via web interface!")
        set_announcement_playing(False) # Attempt to remove the lock file
        flash("Announcement lock forcefully reset.", "warning")
        return jsonify({'success': True, 'message': 'Lock reset successfully.'}), 200
    except Exception as e:
         logging.error(f"Error during force lock reset: {e}")
         return jsonify({'error': f'Failed to reset lock: {str(e)}'}), 500


@app.route('/cache_status')
def cache_status():
    """
    Get statistics about the TTS audio cache (file count, total size).

    Returns:
        JSON: Cache statistics or error message.
    """
    try:
        logging.debug("Requesting cache status.")
        cache_files = []
        total_size = 0
        if os.path.exists(CACHE_DIR) and os.path.isdir(CACHE_DIR):
            for item in os.listdir(CACHE_DIR):
                item_path = os.path.join(CACHE_DIR, item)
                if os.path.isfile(item_path):
                     try:
                         total_size += os.path.getsize(item_path)
                         cache_files.append(item)
                     except OSError as e:
                         logging.warning(f"Could not get size for cache file {item_path}: {e}")
        else:
            logging.info("Cache directory not found for status check.")

        # Calculate size in MB
        cache_size_mb = round(total_size / (1024 * 1024), 2) if total_size > 0 else 0

        status = {
            'cache_count': len(cache_files),
            'cache_size_bytes': total_size,
            'cache_size_mb': cache_size_mb,
            'cache_location': CACHE_DIR
        }
        logging.debug(f"Cache status: {status}")
        return jsonify(status)
    except Exception as e:
        logging.error(f"Error getting cache status: {e}")
        return jsonify({'error': f'Error getting cache status: {str(e)}'}), 500

@app.route('/clear_cache', methods=['POST'])
def clear_cache():
    """
    Handle POST requests to delete all files in the TTS cache directory.
    Triggers regeneration afterwards.
    """
    try:
        logging.warning("Cache clear requested via web interface!")
        files_deleted = 0
        errors_deleting = 0
        if os.path.exists(CACHE_DIR) and os.path.isdir(CACHE_DIR):
            for item in os.listdir(CACHE_DIR):
                item_path = os.path.join(CACHE_DIR, item)
                if os.path.isfile(item_path):
                    try:
                        os.remove(item_path)
                        files_deleted += 1
                        logging.debug(f"Deleted cache file: {item_path}")
                    except OSError as e:
                        errors_deleting += 1
                        logging.error(f"Failed to delete cache file {item_path}: {e}")
            logging.info(f"Cache clear complete. Files deleted: {files_deleted}, Errors: {errors_deleting}")
            flash(f"Cache cleared ({files_deleted} files removed). Regeneration triggered.", 'success')

            # Trigger regeneration in the background after clearing
            logging.info("Triggering background regeneration after cache clear.")
            regeneration_thread = threading.Thread(target=pre_generate_all_announcements, daemon=True)
            regeneration_thread.start()

            return jsonify({'success': True, 'message': f'Cache cleared ({files_deleted} files). Regeneration started.'}), 200
        else:
             logging.info("Cache clear requested, but cache directory does not exist.")
             flash("Cache directory not found.", "info")
             return jsonify({'success': True, 'message': 'Cache directory not found.'}), 200
    except Exception as e:
        logging.error(f"Error clearing cache: {e}")
        flash(f"Error clearing cache: {str(e)}", "error")
        return jsonify({'error': f'Error clearing cache: {str(e)}'}), 500

# --- Error Handlers ---

@app.errorhandler(404)
def page_not_found(e):
    """Custom error handler for 404 Not Found errors."""
    logging.warning(f"404 Not Found error for URL: {request.url}")
    current_time = datetime.datetime.now()
    # Render a user-friendly error page
    return render_template('error.html', error=e, current_year=current_time.year), 404

@app.errorhandler(500)
def internal_server_error(e):
    """Custom error handler for 500 Internal Server errors."""
    logging.error(f"500 Internal Server Error at URL: {request.url}", exc_info=e) # Log exception info
    current_time = datetime.datetime.now()
    # Render a user-friendly error page
    # Be careful not to pass the raw exception 'e' if it might contain sensitive info
    error_info = {'code': 500, 'description': 'An internal server error occurred.'}
    return render_template('error.html', error=error_info, current_year=current_time.year), 500

# --- Application Cleanup ---

def cleanup():
    """Function to run when the Flask application exits."""
    logging.info("Flask application shutting down. Cleaning up...")
    # Ensure the lock file is removed on shutdown, just in case.
    set_announcement_playing(False)
    logging.info("Cleanup complete.")

# Register the cleanup function to run at exit
atexit.register(cleanup)

# --- Main Execution Block ---

if __name__ == '__main__':
    """
    Entry point when the script is executed directly.
    Initializes cleanup, pre-generates announcements, and starts the Flask development server.
    """
    # Perform initial cleanup of any stale lock file from a previous run
    logging.info("Flask application starting up...")
    set_announcement_playing(False)

    # Ensure static and templates directories exist (optional, good practice)
    # os.makedirs('static', exist_ok=True)
    # os.makedirs('templates', exist_ok=True)

    # Pre-generate announcements on startup to ensure they are ready
    # Running this synchronously before starting the server
    pre_generate_all_announcements()

    logging.info("Starting Flask development server on http://0.0.0.0:5000")
    # Start the Flask development server
    # host='0.0.0.0' makes it accessible on the network
    # debug=True enables auto-reloading and detailed error pages (disable in production)
    # Use a proper WSGI server like Gunicorn or Waitress for production deployment.
    app.run(host='0.0.0.0', port=5000, debug=False) # Set debug=False for production/stable use
