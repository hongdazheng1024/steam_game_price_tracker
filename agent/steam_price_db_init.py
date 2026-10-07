import sqlite3
import tomllib
from datetime import datetime
from pathlib import Path

DB_FILE = "steam_tracker.db"
CONFIG_PATH = Path(__file__).parent / "db_config.toml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))

    force_to_drop_tables = raw.get("force_to_drop_tables", False)
    if not isinstance(force_to_drop_tables, bool):
        raise ValueError(
            f"'force_to_drop_tables' must be true or false, got {force_to_drop_tables!r}")

    return {"force_to_drop_tables": force_to_drop_tables}


def add_missing_columns(cursor):
    # CREATE TABLE IF NOT EXISTS leaves an existing table as it was, so add columns added since
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(game_catalog)")}
    if "steam_appid" not in columns:
        cursor.execute("ALTER TABLE game_catalog ADD COLUMN steam_appid INTEGER")


def init_db(force_to_drop_tables: bool = False):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        if force_to_drop_tables:
            cursor.execute("DROP TABLE IF EXISTS game_library")
            cursor.execute("DROP TABLE IF EXISTS game_catalog")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS game_library (
                id TEXT,
                title TEXT NOT NULL,
                country_code TEXT NOT NULL,
                current_deal TEXT,
                historical_lowest TEXT,
                last_search_date TEXT,
                PRIMARY KEY (id, country_code)
            );
        """)
        # ITAD search results: maps official title to game id so later lookups can skip the search API.
        # steam_appid is set for games found through the knowledge base (find_game_candidates);
        # ITAD search results don't include it.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS game_catalog (
                id TEXT PRIMARY KEY,
                official_title TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                steam_appid INTEGER
            );
        """)
        add_missing_columns(cursor)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_game_catalog_normalized_title ON game_catalog (normalized_title)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_game_catalog_steam_appid ON game_catalog (steam_appid)"
        )
        conn.commit()


if __name__ == "__main__":
    config = load_config()
    init_db(force_to_drop_tables=config["force_to_drop_tables"])
