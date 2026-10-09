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

    return {
        "force_to_drop_tables": force_to_drop_tables,
        "history_max_age_hours": positive_number(raw, "history_max_age_hours", 24),
        "game_info_max_age_hours": positive_number(raw, "game_info_max_age_hours", 168),
    }


def positive_number(raw: dict, key: str, default: float) -> float:
    value = raw.get(key, default)
    # bool is a subclass of int, so rule it out explicitly
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"'{key}' must be a positive number, got {value!r}")
    return value


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
            cursor.execute("DROP TABLE IF EXISTS price_history")
            cursor.execute("DROP TABLE IF EXISTS price_history_sync")
            cursor.execute("DROP TABLE IF EXISTS game_info")
            cursor.execute("DROP TABLE IF EXISTS game_companies")
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
        # Steam price changes from ITAD's /games/history/v2, one row per change. Prices are a step
        # function: each row's price holds until the next row. Timestamps are stored in UTC.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                game_id TEXT NOT NULL,
                country_code TEXT NOT NULL,
                shop_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                price REAL NOT NULL,
                regular_price REAL NOT NULL,
                cut INTEGER NOT NULL,
                currency TEXT NOT NULL,
                PRIMARY KEY (game_id, country_code, shop_id, timestamp)
            );
        """)
        # When each game's history was last fetched, so a fresh history skips the API and a stale one
        # only fetches what's new. A row with no price_history rows means the game has no Steam history.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_history_sync (
                game_id TEXT NOT NULL,
                country_code TEXT NOT NULL,
                last_synced_at TEXT NOT NULL,
                PRIMARY KEY (game_id, country_code)
            );
        """)
        # Details from ITAD's /games/info/v2, used to find and compare similar games. Reviews and player
        # counts are a snapshot from fetched_at. tags is a JSON list. Scores are 0-100; NULL if ITAD has none.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS game_info (
                game_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                steam_appid INTEGER,
                release_date TEXT,
                early_access INTEGER NOT NULL,
                tags TEXT NOT NULL,
                steam_score INTEGER,
                steam_review_count INTEGER,
                metascore INTEGER,
                players_recent INTEGER,
                players_peak INTEGER,
                fetched_at TEXT NOT NULL
            );
        """)
        # A game's publishers and developers by ITAD company id. One company can appear under several
        # names ("Capcom", "Capcom Co., Ltd."), but they share an id, so comparables match on company_id.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS game_companies (
                game_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('publisher', 'developer')),
                company_id INTEGER NOT NULL,
                company_name TEXT NOT NULL,
                PRIMARY KEY (game_id, role, company_id)
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_game_companies_company ON game_companies (company_id, role)"
        )
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
