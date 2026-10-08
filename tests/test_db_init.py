import sqlite3

import pytest

import steam_price_db_init
from steam_price_db_init import init_db, load_config


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


def tables(db_file):
    with sqlite3.connect(db_file) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_new_database_has_price_history_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(steam_price_db_init, "DB_FILE", str(tmp_path / "new.db"))
    init_db()
    assert {"price_history", "price_history_sync"} <= tables(tmp_path / "new.db")


def test_force_drop_clears_price_history(tmp_path, monkeypatch):
    db_file = tmp_path / "old.db"
    monkeypatch.setattr(steam_price_db_init, "DB_FILE", str(db_file))
    init_db()
    with sqlite3.connect(db_file) as conn:
        conn.execute("INSERT INTO price_history_sync VALUES ('id1', 'US', '2026-01-01T00:00:00+00:00')")

    init_db(force_to_drop_tables=True)

    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT COUNT(*) FROM price_history_sync").fetchone() == (0,)


def write_config(tmp_path, text):
    path = tmp_path / "db_config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_history_max_age_defaults_to_24_hours(tmp_path):
    assert load_config(write_config(tmp_path, ""))["history_max_age_hours"] == 24


@pytest.mark.parametrize("value", ["0", "-1", "true", '"24"'])
def test_invalid_history_max_age_is_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, f"history_max_age_hours = {value}"))
