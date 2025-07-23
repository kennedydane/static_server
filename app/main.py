import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from threading import Thread

from flask import Flask, jsonify, render_template
from loguru import logger
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# --- Configuration ---
STATIC_FOLDER = Path("static")
CACHE_FILE = Path("cache/checksums.json")
WATCH_DELAY = 300  # 5 minutes, as requested

# --- Initialization ---
app = Flask(__name__)

def setup_logging(log_file):
    """Configures the application logger."""
    logger.remove() # Remove the default handler
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    logger.add(
        log_path,
        rotation="10 MB",    # Rotate the file when it reaches 10 MB
        retention="30 days", # Keep logs for up to 30 days
        compression="zip",   # Compress old log files
        level="INFO",        # Set the minimum log level to INFO
        enqueue=True,        # Make logging non-blocking
        backtrace=True,      # Show full stack traces on errors
        diagnose=True        # Add exception variable values
    )
    logger.info("Logging configured.")

# Ensure necessary directories and files exist
STATIC_FOLDER.mkdir(exist_ok=True)
CACHE_FILE.parent.mkdir(exist_ok=True)
if not CACHE_FILE.exists():
    with open(CACHE_FILE, "w") as f:
        json.dump({}, f)


# --- Checksum Logic ---
def calculate_checksums(file_path):
    """Calculates MD5 and SHA256 checksums for a file."""
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                md5.update(chunk)
                sha256.update(chunk)
        return md5.hexdigest(), sha256.hexdigest()
    except (IOError, OSError) as e:
        logger.error(f"Error reading file {file_path}: {e}")
        return None, None


def update_checksum_cache():
    """Scans the static folder and updates the checksum cache."""
    logger.info("Updating checksum cache...")
    try:
        with open(CACHE_FILE, "r+") as f:
            try:
                cache_data = json.load(f)
            except json.JSONDecodeError:
                cache_data = {}

            # Remove files from cache that no longer exist
            for filename in list(cache_data.keys()):
                if not (STATIC_FOLDER / filename).exists():
                    del cache_data[filename]
                    logger.info(f"Removed {filename} from cache.")

            # Add new/modified files to cache
            for file_path in STATIC_FOLDER.iterdir():
                if file_path.is_file():
                    filename = file_path.name
                    mod_time = file_path.stat().st_mtime
                    if (
                        filename not in cache_data
                        or mod_time > cache_data.get(filename, {}).get("mod_time", 0)
                    ):
                        md5, sha256 = calculate_checksums(file_path)
                        if md5 and sha256:
                            cache_data[filename] = {
                                "md5": md5,
                                "sha256": sha256,
                                "mod_time": mod_time,
                                "size": file_path.stat().st_size,
                            }
                            logger.info(f"Updated checksums for {filename}.")
            
            f.seek(0)
            json.dump(cache_data, f, indent=4)
            f.truncate()
        logger.info("Checksum cache update complete.")
    except (IOError, OSError) as e:
        logger.error(f"Error updating cache file: {e}")


# --- Filesystem Watcher ---
class ChangeHandler(FileSystemEventHandler):
    """Handler for filesystem events."""
    def on_any_event(self, event):
        logger.info(f"Detected filesystem change: {event.event_type} on {event.src_path}")
        update_checksum_cache()


def start_watcher():
    """Starts the filesystem watcher in a separate thread."""
    event_handler = ChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, str(STATIC_FOLDER), recursive=False)
    observer.start()
    logger.info(f"Started watching {STATIC_FOLDER} for changes.")
    try:
        while True:
            time.sleep(WATCH_DELAY)
            update_checksum_cache()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


# --- Flask Routes ---
@app.route("/")
def index():
    """Renders the main page."""
    return render_template("index.html")


@app.route("/api/files")
def get_files_json():
    """Returns the list of files and their checksums from the cache as JSON."""
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        return jsonify(data)
    except (IOError, json.JSONDecodeError) as e:
        logger.error(f"Could not read or parse cache file: {e}")
        return jsonify({}), 500

@app.route("/api/files/table")
def get_files_table():
    """Returns the list of files and their checksums as an HTML table."""
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        return render_template("file_list.html", files=data)
    except (IOError, json.JSONDecodeError) as e:
        logger.error(f"Could not read or parse cache file: {e}")
        return "Error loading file data.", 500


# --- Main Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simple Static File Server")
    parser.add_argument(
        "--log-file",
        default="logs/app.log",
        help="Path to the log file."
    )
    args = parser.parse_args()

    setup_logging(args.log_file)
    
    # Initial cache update
    update_checksum_cache()

    # Start the watcher in a background thread
    watcher_thread = Thread(target=start_watcher, daemon=True)
    watcher_thread.start()

    # Run the Flask app
    app.run(host="0.0.0.0", port=5000)