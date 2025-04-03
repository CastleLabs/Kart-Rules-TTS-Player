#!/usr/bin/env python3
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
import os
import logging
import asyncio
import tempfile
import subprocess
import edge_tts
import time
import datetime
import threading
import atexit
import hashlib
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("announcement_script.log"),
        logging.StreamHandler()
    ]
)

# Shared state file path (same as in kartrules.py)
ANNOUNCEMENT_LOCK_FILE = "/tmp/announcement_playing.lock"

# Directory for cached TTS files
CACHE_DIR = "/tmp/tts_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# Global variables for announcement control
last_announcement_time = 0
ANNOUNCEMENT_COOLDOWN = 8.0  # seconds to wait between announcements

# Initialize Flask application
app = Flask(__name__, 
            static_folder='static',
            template_folder='templates')
app.secret_key = 'your_secret_key_here'  # Change this in production!

# Application startup time (for uptime calculation)
start_time = time.time()

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

class ConfigHandler:
    def __init__(self, config_file: str = "config.ini"):
        self.config_file = config_file
        self.config = {
            'announcements': {
                'button1': '',
                'button2': '',
                'button3': ''
            },
            'tts': {
                'voice_id': '',
                'output_format': 'mp3'
            },
            'gpio': {
                'button1': 17,
                'button2': 27,
                'button3': 22
            }
        }

    def read_config(self):
        """Read configuration from the config file."""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    current_section = None
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        if line.startswith('[') and line.endswith(']'):
                            current_section = line[1:-1].lower()
                            continue
                        if '=' in line:
                            key, value = [x.strip() for x in line.split('=', 1)]
                            value = value.strip('"\'')
                            if current_section == 'announcements':
                                self.config['announcements'][key.lower()] = value
                            elif current_section == 'tts':
                                self.config['tts'][key.lower()] = value
                            elif current_section == 'gpio':
                                try:
                                    self.config['gpio'][key.lower()] = int(value)
                                except ValueError:
                                    logging.warning(f"Invalid GPIO pin value for {key}: {value}")
            return self.config
        except Exception as e:
            logging.error(f"Error reading config: {e}")
            return self.config

    def write_config(self):
        """Write configuration to the config file."""
        try:
            with open(self.config_file, 'w') as f:
                f.write("[announcements]\n")
                for key, value in self.config['announcements'].items():
                    f.write(f"{key} = {value}\n")
                f.write("\n[tts]\n")
                for key, value in self.config['tts'].items():
                    f.write(f"{key} = {value}\n")
                f.write("\n[gpio]\n")
                for key, value in self.config['gpio'].items():
                    f.write(f"{key} = {value}\n")
        except Exception as e:
            logging.error(f"Error writing config: {e}")
            raise

async def synthesize_speech_async(text: str, voice_id: str, output_path: str) -> bool:
    """Generate speech from text using Microsoft Edge TTS."""
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

def play_sound(sound_path: str, output_format: str = 'mp3') -> bool:
    """Play sound file using mpg123."""
    if not sound_path or not os.path.exists(sound_path):
        logging.error(f"Invalid sound path: {sound_path}")
        return False
    try:
        logging.info(f"Playing sound file: {sound_path}")
        # Check if mpg123 is installed
        if subprocess.run(['which', 'mpg123'], capture_output=True).returncode != 0:
            logging.error("mpg123 is not installed")
            return False
        
        # Play the sound
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
        # This ensures that after the sound is done playing, the lock is cleared
        set_announcement_playing(False)

def get_system_uptime():
    """Calculate system uptime in a readable format."""
    uptime_seconds = time.time() - start_time
    return str(datetime.timedelta(seconds=int(uptime_seconds)))

def pre_generate_announcement(text: str, voice_id: str):
    """Generate a single announcement file for caching."""
    if not text or not voice_id:
        return None
    
    try:
        cache_file = get_cache_filename(text, voice_id)
        
        # Check if cache file exists and is valid
        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
            logging.info(f"Using existing cached file: {cache_file}")
            return cache_file
        
        # Need to generate a new file
        logging.info(f"Generating new speech file for: {text[:50]}...")
        
        # Create the speech file synchronously
        success = asyncio.run(synthesize_speech_async(text, voice_id, cache_file))
        if success:
            logging.info(f"Successfully generated cached file: {cache_file}")
            return cache_file
        else:
            if os.path.exists(cache_file):
                os.remove(cache_file)
            logging.error("Failed to generate speech file")
            return None
    except Exception as e:
        logging.error(f"Error generating speech file: {e}")
        return None

def pre_generate_all_announcements():
    """Pre-generate all announcement files based on current configuration."""
    logging.info("Pre-generating all announcement files...")
    config_handler = ConfigHandler()
    config = config_handler.read_config()
    voice_id = config['tts']['voice_id']
    
    if not voice_id:
        logging.error("Voice ID not configured, skipping pre-generation")
        return
    
    for button_id, text in config['announcements'].items():
        if text:
            logging.info(f"Pre-generating announcement for {button_id}...")
            speech_file = pre_generate_announcement(text, voice_id)
            if speech_file:
                logging.info(f"Generated/verified speech file for {button_id}")
            else:
                logging.error(f"Failed to generate speech file for {button_id}")
    
    logging.info("Finished pre-generating announcements")

@app.route('/')
def index():
    """Render the main configuration page."""
    config_handler = ConfigHandler()
    config = config_handler.read_config()
    
    # Pass current time directly instead of the datetime module
    current_time = datetime.datetime.now()
    
    return render_template(
        'config.html', 
        announcements=config['announcements'], 
        tts=config['tts'],
        gpio=config['gpio'],
        current_time=current_time,
        current_year=current_time.year,
        uptime=get_system_uptime()
    )

@app.route('/save_config', methods=['POST'])
def save_config():
    """Save configuration changes."""
    try:
        config_handler = ConfigHandler()
        config = config_handler.read_config()
        
        # Get the old announcements to check for changes
        old_announcements = config['announcements'].copy()
        old_voice_id = config['tts']['voice_id']
        
        # Update announcement texts
        config['announcements']['button1'] = request.form['button1']
        config['announcements']['button2'] = request.form['button2']
        config['announcements']['button3'] = request.form['button3']
        
        # Update TTS settings
        config['tts']['voice_id'] = request.form['voice_id']
        config['tts']['output_format'] = request.form['output_format']
        
        # Save to config file
        config_handler.config = config
        config_handler.write_config()
        
        # Check if we need to regenerate any announcements
        voice_changed = old_voice_id != config['tts']['voice_id']
        
        # Automatically restart the service after saving
        logging.info("Configuration saved, automatically restarting service")
        
        # Clean up any stale lock files
        set_announcement_playing(False)
        
        # Start a background thread to regenerate all announcements
        threading.Thread(target=pre_generate_all_announcements).start()
        
        if voice_changed:
            # Voice changed, need to regenerate all announcements
            logging.info("Voice ID changed, regenerated all announcements with new voice")
            flash('Configuration saved successfully! Service restarted with new voice.', 'success')
        else:
            # Check if any announcement texts changed
            changed_announcements = []
            for button_id, text in config['announcements'].items():
                if text != old_announcements.get(button_id, ""):
                    changed_announcements.append(button_id)
                    
            if changed_announcements:
                logging.info(f"Announcements changed for buttons: {', '.join(changed_announcements)}")
                flash('Configuration saved successfully! Service restarted with updated announcements.', 'success')
            else:
                flash('Configuration saved successfully! Service restarted.', 'success')
        
        return redirect(url_for('index'))
    except Exception as e:
        flash(f'Error saving configuration: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/play_instant', methods=['POST'])
def play_instant():
    """Play an announcement immediately."""
    global last_announcement_time
    
    # Check if any announcement is already playing (from either script)
    if is_announcement_playing():
        logging.info("Announcement request ignored - another announcement is already playing")
        return jsonify({'error': 'Another announcement is currently playing'}), 429
    
    # Check if enough time has passed since the last announcement
    current_time = time.time()
    if current_time - last_announcement_time < ANNOUNCEMENT_COOLDOWN:
        logging.info(f"Announcement request ignored - too soon after previous announcement")
        return jsonify({'error': 'Please wait before playing another announcement'}), 429
    
    # Update the last announcement time
    last_announcement_time = current_time
    
    # Important: Set the lock file BEFORE doing anything else
    set_announcement_playing(True)
    
    try:
        # Get the announcement text from the request
        data = request.get_json()
        if not data or 'text' not in data:
            set_announcement_playing(False)
            return jsonify({'error': 'Missing text parameter'}), 400
        
        text = data['text']
        if not text:
            set_announcement_playing(False)
            return jsonify({'error': 'Empty announcement text'}), 400
        
        # Get voice ID from config
        config_handler = ConfigHandler()
        config = config_handler.read_config()
        voice_id = config['tts']['voice_id']
        output_format = config['tts']['output_format']
        
        # Get the cached file path
        cache_file = get_cache_filename(text, voice_id)
        
        # Check if we need to generate the file
        if not os.path.exists(cache_file) or os.path.getsize(cache_file) == 0:
            # File doesn't exist or is empty, generate it
            logging.info(f"Cache miss - generating new speech file for: {text[:50]}...")
            success = asyncio.run(synthesize_speech_async(text, voice_id, cache_file))
            if not success:
                if os.path.exists(cache_file):
                    os.remove(cache_file)
                set_announcement_playing(False)
                return jsonify({'error': 'Failed to synthesize speech'}), 500
        
        # Play the sound (the lock file will be cleared in play_sound)
        if play_sound(cache_file, output_format):
            return jsonify({'success': True, 'message': 'Announcement played successfully'}), 200
        else:
            set_announcement_playing(False)
            return jsonify({'error': 'Failed to play announcement'}), 500
    except Exception as e:
        logging.error(f"Error in play_instant: {e}")
        set_announcement_playing(False)
        return jsonify({'error': str(e)}), 500

@app.route('/announcement_status')
def announcement_status():
    """Check if an announcement is currently playing (checks both web UI and physical buttons)."""
    global last_announcement_time
    
    current_time = time.time()
    time_since_last = current_time - last_announcement_time
    cooldown_active = time_since_last < ANNOUNCEMENT_COOLDOWN
    
    # Also check if any announcement is playing (from either script)
    any_playing = is_announcement_playing()
    
    return jsonify({
        'is_playing': cooldown_active or any_playing,
        'seconds_remaining': max(0, ANNOUNCEMENT_COOLDOWN - time_since_last) if cooldown_active else 0,
        'lock_file_exists': any_playing
    })

@app.route('/logs')
def get_logs():
    """Return the contents of the log file."""
    try:
        log_file = "announcement_script.log"
        if not os.path.exists(log_file):
            return "No logs found", 404
        
        # Get the last 100 lines of the log file
        with open(log_file, 'r') as f:
            lines = f.readlines()
            last_lines = lines[-100:] if len(lines) > 100 else lines
            
        return ''.join(last_lines)
    except Exception as e:
        logging.error(f"Error reading logs: {e}")
        return str(e), 500

@app.route('/download_logs')
def download_logs():
    """Download the log file."""
    try:
        log_file = "announcement_script.log"
        if not os.path.exists(log_file):
            flash('Log file not found', 'error')
            return redirect(url_for('index'))
        
        return send_file(log_file, as_attachment=True)
    except Exception as e:
        logging.error(f"Error downloading logs: {e}")
        flash(f'Error downloading logs: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/restart_service', methods=['POST'])
def restart_service():
    """Restart the announcement service (placeholder)."""
    try:
        # In a real implementation, this would restart your GPIO service
        # For this demo, we'll just log it
        logging.info("Service restart requested")
        
        # Clean up any stale lock files
        set_announcement_playing(False)
        
        # Regenerate all announcements
        threading.Thread(target=pre_generate_all_announcements).start()
        
        # Simulate a restart
        time.sleep(2)
        
        return jsonify({'success': True, 'message': 'Service restarted successfully'}), 200
    except Exception as e:
        logging.error(f"Error restarting service: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/check_dependencies')
def check_dependencies():
    """Check if required dependencies are installed."""
    dependencies = {
        'mpg123': False,
        'edge_tts': True  # We know edge_tts is installed since we imported it
    }
    
    # Check mpg123
    try:
        result = subprocess.run(['which', 'mpg123'], capture_output=True)
        dependencies['mpg123'] = result.returncode == 0
    except Exception:
        pass
    
    return jsonify(dependencies)

@app.route('/reset_locks', methods=['POST'])
def reset_locks():
    """Emergency endpoint to reset all locks."""
    set_announcement_playing(False)
    logging.warning("Manual lock reset performed via web interface")
    return jsonify({'success': True}), 200

@app.route('/cache_status')
def cache_status():
    """Get information about the TTS cache."""
    try:
        cache_files = os.listdir(CACHE_DIR) if os.path.exists(CACHE_DIR) else []
        total_size = 0
        
        for file in cache_files:
            file_path = os.path.join(CACHE_DIR, file)
            if os.path.isfile(file_path):
                total_size += os.path.getsize(file_path)
        
        return jsonify({
            'cache_count': len(cache_files),
            'cache_size_bytes': total_size,
            'cache_size_mb': round(total_size / (1024 * 1024), 2),
            'cache_location': CACHE_DIR
        })
    except Exception as e:
        logging.error(f"Error getting cache status: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/clear_cache', methods=['POST'])
def clear_cache():
    """Clear the TTS cache files."""
    try:
        if os.path.exists(CACHE_DIR):
            for file in os.listdir(CACHE_DIR):
                file_path = os.path.join(CACHE_DIR, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
        
        # Regenerate the announcements
        threading.Thread(target=pre_generate_all_announcements).start()
        
        return jsonify({'success': True, 'message': 'Cache cleared successfully. Regenerating announcements.'}), 200
    except Exception as e:
        logging.error(f"Error clearing cache: {e}")
        return jsonify({'error': str(e)}), 500

@app.errorhandler(404)
def page_not_found(e):
    """Handle 404 errors."""
    current_time = datetime.datetime.now()
    return render_template('error.html', error=e, current_year=current_time.year), 404

@app.errorhandler(500)
def internal_server_error(e):
    """Handle 500 errors."""
    current_time = datetime.datetime.now()
    return render_template('error.html', error=e, current_year=current_time.year), 500

def cleanup():
    """Clean up resources when the app exits."""
    # Make sure lock file is removed
    set_announcement_playing(False)

# Register cleanup function
atexit.register(cleanup)

if __name__ == '__main__':
    # Clean up any stale lock files at startup
    set_announcement_playing(False)
    
    # Make sure the static directory exists
    os.makedirs('static', exist_ok=True)
    
    # Make sure the templates directory exists
    os.makedirs('templates', exist_ok=True)
    
    # Pre-generate all announcements
    pre_generate_all_announcements()
    
    # Start the Flask app
    app.run(host='0.0.0.0', port=5000, debug=True)
