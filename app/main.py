import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from queue import Queue
from threading import Thread, Lock

from flask import Flask, Response, jsonify, render_template
from loguru import logger
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# --- Global Configuration ---
STATIC_FOLDER = None
CACHE_FILE = Path("cache/checksums.json")
checksum_cache = {}
sse_clients = []
sse_clients_lock = Lock()
config_cache = {}
config_cache_lock = Lock()


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
def build_file_tree(files_dict):
    """Build a nested tree structure from flat file paths."""
    tree = {}
    
    for filepath, data in files_dict.items():
        parts = filepath.split('/')
        current = tree
        
        # Navigate/create the directory structure
        for i, part in enumerate(parts[:-1]):
            if part not in current:
                current[part] = {'type': 'directory', 'children': {}, 'path': '/'.join(parts[:i+1])}
            current = current[part]['children']
        
        # Add the file
        filename = parts[-1]
        current[filename] = {
            'type': 'file',
            'path': filepath,
            'data': data
        }
    
    return tree


def format_file_size(size_bytes):
    """Convert bytes to human readable format."""
    if size_bytes == 0:
        return "0 B"
    
    size_names = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    
    return f"{size_bytes:.1f} {size_names[i]}"


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

    # Set of files currently on disk (recursive)
    files_on_disk = {}
    for file_path in STATIC_FOLDER.rglob("*"):
        if file_path.is_file():
            # Use relative path from static folder as the key
            rel_path = file_path.relative_to(STATIC_FOLDER)
            files_on_disk[str(rel_path)] = file_path

    # Remove files from cache that no longer exist on disk
    for filename in list(checksum_cache.keys()):
        if filename not in files_on_disk:
            del checksum_cache[filename]
            logger.info(f"Removed {filename} from cache.")

    # Add new files and update modified files
    for filename, file_path in files_on_disk.items():
        mod_time = file_path.stat().st_mtime

        if (
            filename not in checksum_cache or
            mod_time > checksum_cache[filename].get("mod_time", 0)
        ):
            md5, sha256 = calculate_checksums(file_path)
            if md5 and sha256:
                file_size = file_path.stat().st_size
                checksum_cache[filename] = {
                    "md5": md5,
                    "sha256": sha256,
                    "mod_time": mod_time,
                    "size": file_size,
                    "size_human": format_file_size(file_size),
                }
                logger.info(f"Updated checksums for {filename}.")

    # If the cache has changed, persist to file and notify clients
    if checksum_cache != old_cache:
        logger.info("Checksum cache has changed. Persisting and notifying clients.")
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(checksum_cache, f, indent=4)
            logger.info("Checksum cache persisted to file.")
            broadcast_sse_update("update")
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
    observer.schedule(event_handler, str(STATIC_FOLDER), recursive=True)
    observer.start()
    logger.info(f"Started watching {STATIC_FOLDER} for changes.")
    try:
        observer.join()
    except KeyboardInterrupt:
        observer.stop()
        observer.join()


# --- Dynamic Configuration ---
CONFIG_FOLDER = Path("config")

def get_config_data():
    """Loads dynamic configuration from the config folder with caching."""
    global config_cache
    
    with config_cache_lock:
        # Check if we need to refresh the cache
        cache_needs_refresh = False
        
        if not config_cache:
            cache_needs_refresh = True
        else:
            # Check modification times of config files
            config_files = list(CONFIG_FOLDER.glob("*"))
            for config_file in config_files:
                if config_file.is_file():
                    mod_time = config_file.stat().st_mtime
                    cached_mod_time = config_cache.get('file_mod_times', {}).get(str(config_file), 0)
                    if mod_time > cached_mod_time:
                        cache_needs_refresh = True
                        break
        
        if not cache_needs_refresh:
            return config_cache['data']
        
        logger.info("Refreshing config cache...")
        
    # Build fresh config data
    config_data = {
        "logos": [],
        "organisation": "My Organisation",
        "heading": "File Distribution Service",
        "text": "Welcome to the file distribution service.",
        "footer_links": []
    }

    CONFIG_FOLDER.mkdir(exist_ok=True)
    file_mod_times = {}

    # Load organisation name
    org_file = CONFIG_FOLDER / "organisation.txt"
    try:
        config_data["organisation"] = org_file.read_text().strip()
        file_mod_times[str(org_file)] = org_file.stat().st_mtime
    except FileNotFoundError:
        logger.warning("organisation.txt not found, using default.")
    except Exception as e:
        logger.error(f"Error reading organisation.txt: {e}")

    # Load heading
    heading_file = CONFIG_FOLDER / "heading.txt"
    try:
        config_data["heading"] = heading_file.read_text().strip()
        file_mod_times[str(heading_file)] = heading_file.stat().st_mtime
    except FileNotFoundError:
        logger.warning("heading.txt not found, using default.")
    except Exception as e:
        logger.error(f"Error reading heading.txt: {e}")

    # Load text
    text_file = CONFIG_FOLDER / "text.txt"
    try:
        config_data["text"] = text_file.read_text().strip()
        file_mod_times[str(text_file)] = text_file.stat().st_mtime
    except FileNotFoundError:
        logger.warning("text.txt not found, using default.")
    except Exception as e:
        logger.error(f"Error reading text.txt: {e}")

    # Load logos
    logo_files = sorted(list(CONFIG_FOLDER.glob("*.logo.*")))
    for logo_path in logo_files:
        try:
            with open(logo_path, "rb") as f:
                # Base64 encode the image for direct embedding in HTML
                import base64
                encoded_logo = base64.b64encode(f.read()).decode("utf-8")
                
                # Determine MIME type based on file extension
                file_ext = logo_path.suffix.lower()
                if file_ext in ['.svg']:
                    mime_type = "image/svg+xml"
                elif file_ext in ['.png']:
                    mime_type = "image/png"
                elif file_ext in ['.jpg', '.jpeg']:
                    mime_type = "image/jpeg"
                elif file_ext in ['.gif']:
                    mime_type = "image/gif"
                elif file_ext in ['.webp']:
                    mime_type = "image/webp"
                else:
                    # Default to PNG for unknown extensions
                    mime_type = "image/png"
                    logger.warning(f"Unknown image extension {file_ext} for {logo_path}, defaulting to PNG")
                
                config_data["logos"].append(f"data:{mime_type};base64,{encoded_logo}")
                file_mod_times[str(logo_path)] = logo_path.stat().st_mtime
        except Exception as e:
            logger.error(f"Error reading or encoding logo {logo_path}: {e}")


    # Load footer links
    link_files = sorted(list(CONFIG_FOLDER.glob("*.link")))
    for link_path in link_files:
        try:
            lines = link_path.read_text().strip().split("\n")
            if len(lines) >= 2:
                config_data["footer_links"].append({"url": lines[0], "text": lines[1]})
                file_mod_times[str(link_path)] = link_path.stat().st_mtime
            else:
                logger.warning(f"Skipping malformed link file: {link_path}")
        except Exception as e:
            logger.error(f"Error reading link file {link_path}: {e}")
    
    # Update cache
    with config_cache_lock:
        config_cache['data'] = config_data
        config_cache['file_mod_times'] = file_mod_times
        logger.info("Config cache updated.")
            
    return config_data


# --- Flask Routes ---
@app.route("/")
def index():
    """Renders the main page with dynamic configuration."""
    config = get_config_data()
    return render_template("index.html", config=config)


def broadcast_sse_update(message):
    """Broadcasts a message to all connected SSE clients."""
    with sse_clients_lock:
        disconnected_clients = []
        for client_queue in sse_clients:
            try:
                client_queue.put_nowait(message)
            except Exception:
                # Client queue is full or closed, mark for removal
                disconnected_clients.append(client_queue)
        
        # Remove disconnected clients
        for client_queue in disconnected_clients:
            sse_clients.remove(client_queue)
            logger.info(f"Removed disconnected SSE client. Active clients: {len(sse_clients)}")


@app.route("/api/events")
def sse_events():
    """Endpoint for Server-Sent Events to notify clients of updates."""
    client_queue = Queue(maxsize=100)
    
    # Register this client
    with sse_clients_lock:
        sse_clients.append(client_queue)
        logger.info(f"New SSE client connected. Active clients: {len(sse_clients)}")
    
    def event_stream():
        try:
            # Send initial connection confirmation
            yield f"event: connected\ndata: connected\n\n"
            
            while True:
                try:
                    message = client_queue.get(timeout=30)  # 30 second timeout
                    yield f"event: update\ndata: {message}\n\n"
                except Exception:
                    # Timeout or other error, send keepalive
                    yield f"event: keepalive\ndata: ping\n\n"
        except Exception as e:
            logger.error(f"SSE client disconnected: {e}")
        finally:
            # Clean up this client
            with sse_clients_lock:
                if client_queue in sse_clients:
                    sse_clients.remove(client_queue)
                    logger.info(f"SSE client disconnected. Active clients: {len(sse_clients)}")
    
    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/api/files")
def get_files_json():
    """Returns the list of files and their checksums from the in-memory cache as JSON."""
    sorted_files = dict(sorted(checksum_cache.items()))
    return jsonify(sorted_files)


@app.route("/api/files/table")
def get_files_table():
    """Returns the list of files and their checksums as an HTML table."""
    sorted_files = dict(sorted(checksum_cache.items()))
    return render_template("file_list.html", files=sorted_files)


@app.route("/api/files/tree")
def get_files_tree():
    """Returns the file tree structure as HTML."""
    sorted_files = dict(sorted(checksum_cache.items()))
    tree = build_file_tree(sorted_files)
    return render_template("file_tree.html", tree=tree)


@app.route("/api/files/tree/<path:directory>")
def get_directory_contents(directory):
    """Returns the contents of a specific directory for expansion."""
    sorted_files = dict(sorted(checksum_cache.items()))
    tree = build_file_tree(sorted_files)
    
    # Navigate to the requested directory
    current = tree
    for part in directory.split('/'):
        if part in current and current[part]['type'] == 'directory':
            current = current[part]['children']
        else:
            return "Directory not found", 404
    
    return render_template("directory_contents.html", tree=current, parent_path=directory)


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
