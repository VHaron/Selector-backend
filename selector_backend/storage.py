import json
import os
import tempfile
import threading
from .config import logger

db_lock = threading.RLock()

def get_db_path(filename):
    return f"/data/{filename}" if os.path.exists("/data") else filename

PRESETS_FILE = get_db_path("presets_db.json")
SETTINGS_FILE = get_db_path("settings_db.json")
NOTES_FILE = get_db_path("notes_db.json")
SESSIONS_HISTORY_FILE = get_db_path("sessions_history_db.json")
GRID_FILE = get_db_path("grid_db.json")
SAVED_GRIDS_FILE = get_db_path("saved_grids_db.json")
BALANCES_FILE = get_db_path("balances_db.json")

def load_json_db(file_path):
    """Load a JSON object; a damaged or missing file starts with an empty store."""
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            logger.warning("Ignoring %s: expected a JSON object", file_path)
            return {}
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Could not load %s: %s", file_path, exc)
        return {}

def save_json_db(file_path, data):
    """Write atomically so a restart cannot leave a partially-written JSON file."""
    directory = os.path.dirname(os.path.abspath(file_path))
    os.makedirs(directory, exist_ok=True)
    with db_lock:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, prefix=".db-", suffix=".tmp", delete=False) as file:
                temp_path = file.name
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, file_path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

user_presets = load_json_db(PRESETS_FILE)
user_settings = load_json_db(SETTINGS_FILE)
user_notes = load_json_db(NOTES_FILE)
user_sessions_history = load_json_db(SESSIONS_HISTORY_FILE)
user_grid = load_json_db(GRID_FILE)
user_saved_grids = load_json_db(SAVED_GRIDS_FILE)
user_balances = load_json_db(BALANCES_FILE)
