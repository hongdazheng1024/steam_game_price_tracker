import sqlite3

import steam_price_db_init
from steam_price_db_init import init_db


def columns(db_file):
    with sqlite3.connect(db_file) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(game_catalog)")}


def test_new_database_has_steam_appid(tmp_path, monkeypatch):
    monkeypatch.setattr(steam_price_db_init, "DB_FILE", str(tmp_path / "new.db"))
    init_db()
    assert "steam_appid" in columns(tmp_path / "new.db")


def test_old_catalog_gets_steam_appid_and_keeps_rows(tmp_path, monkeypatch):
    db_file = tmp_path / "old.db"
    with sqlite3.connect(db_file) as conn:
        conn.execute("""CREATE TABLE game_catalog (id TEXT PRIMARY KEY, official_title TEXT NOT NULL,
                        normalized_title TEXT NOT NULL, created_at TEXT NOT NULL)""")
        conn.execute("INSERT INTO game_catalog VALUES ('id1', 'Hades', 'hades', '2026-01-01')")
    monkeypatch.setattr(steam_price_db_init, "DB_FILE", str(db_file))

    init_db()
    init_db()  # running twice is safe

    assert "steam_appid" in columns(db_file)
    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT id, steam_appid FROM game_catalog").fetchall() == [("id1", None)]
