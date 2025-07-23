import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from queue import Queue
from threading import Thread

from flask import Flask, Response, jsonify, render_template
from loguru import logger
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# --- Global Configuration ---
STATIC_FOLDER = None
CACHE_FILE = Path("cache/checksums.json")
checksum_cache = {}
sse_queue = Queue()


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
    """
    Scans the static folder, updates the in-memory cache, and persists it to a file.
    Notifies clients of changes via SSE.
    """
    global checksum_cache
    logger.info("Scanning for file changes...")

    # Make a copy of the current cache to compare against later
    old_cache = checksum_cache.copy()

    # Set of files currently on disk
    files_on_disk = {p.name for p in STATIC_FOLDER.iterdir() if p.is_file()}

    # Remove files from cache that no longer exist on disk
    for filename in list(checksum_cache.keys()):
        if filename not in files_on_disk:
            del checksum_cache[filename]
            logger.info(f"Removed {filename} from cache.")

    # Add new files and update modified files
    for filename in files_on_disk:
        file_path = STATIC_FOLDER / filename
        mod_time = file_path.stat().st_mtime

        if (
            filename not in checksum_cache or
            mod_time > checksum_cache[filename].get("mod_time", 0)
        ):
            md5, sha256 = calculate_checksums(file_path)
            if md5 and sha256:
                checksum_cache[filename] = {
                    "md5": md5,
                    "sha256": sha256,
                    "mod_time": mod_time,
                    "size": file_path.stat().st_size,
                }
                logger.info(f"Updated checksums for {filename}.")

    # If the cache has changed, persist to file and notify clients
    if checksum_cache != old_cache:
        logger.info("Checksum cache has changed. Persisting and notifying clients.")
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(checksum_cache, f, indent=4)
            logger.info("Checksum cache persisted to file.")
            sse_queue.put("update")
        except (IOError, OSError) as e:
            logger.error(f"Error writing cache file: {e}")
    else:
        logger.info("No changes to checksum cache detected.")


def initial_cache_load():
    """Loads the cache from the file into memory at startup."""
    global checksum_cache
    try:
        with open(CACHE_FILE, "r") as f:
            checksum_cache = json.load(f)
        logger.info("Initial checksum cache loaded from file.")
    except (IOError, json.JSONDecodeError):
        logger.warning(f"Could not load cache file, will perform a fresh scan.")
        checksum_cache = {}


# --- Filesystem Watcher ---
class ChangeHandler(FileSystemEventHandler):
    """Handler for filesystem events, triggering updates only on finished operations."""

    def on_closed(self, event):
        """Called when a file is closed (after writing)."""
        if not event.is_directory:
            logger.info(f"File closed: {event.src_path}. Triggering cache update.")
            update_checksum_cache()

    def on_deleted(self, event):
        """Called when a file or directory is deleted."""
        if not event.is_directory:
            logger.info(f"File deleted: {event.src_path}. Triggering cache update.")
            update_checksum_cache()

    def on_moved(self, event):
        """Called when a file or directory is moved or renamed."""
        if not event.is_directory:
            logger.info(f"File moved: {event.src_path} to {event.dest_path}. Triggering cache update.")
            update_checksum_cache()


def start_watcher():
    """Starts the filesystem watcher in a separate thread."""
    event_handler = ChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, str(STATIC_FOLDER), recursive=False)
    observer.start()
    logger.info(f"Started watching {STATIC_FOLDER} for changes.")
    try:
        observer.join()
    except KeyboardInterrupt:
        observer.stop()
        observer.join()


# --- Flask Routes ---
@app.route("/")
def index():
    """Renders the main page."""
    return render_template("index.html")


@app.route("/api/events")
def sse_events():
    """Endpoint for Server-Sent Events to notify clients of updates."""
    def event_stream():
        while True:
            message = sse_queue.get() # Blocks until a message is available
            yield f"event: update\ndata: {message}\n\n"
    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/api/files")
def get_files_json():
    """Returns the list of files and their checksums from the in-memory cache as JSON."""
    return jsonify(checksum_cache)


@app.route("/api/files/table")
def get_files_table():
    """Returns the list of files and their checksums as an HTML table."""
    return render_template("file_list.html", files=checksum_cache)


# --- Main Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simple Static File Server")
    parser.add_argument(
        "--static-folder",
        default="static",
        help="Path to the folder containing static files to serve."
    )
    parser.add_argument(
        "--log-file",
        default="logs/app.log",
        help="Path to the log file."
    )
    args = parser.parse_args()

    # Set global configuration from arguments
    STATIC_FOLDER = Path(args.static_folder)

    setup_logging(args.log_file)
    
    # Load initial cache and perform first scan
    initial_cache_load()
    update_checksum_cache()

    # Start the watcher in a background thread
    watcher_thread = Thread(target=start_watcher, daemon=True)
    watcher_thread.start()

    # Run the Flask app
    app.run(host="0.0.0.0", port=5000)
