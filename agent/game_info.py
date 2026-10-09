"""Game details from IsThereAnyDeal, cached in the game_info and game_companies tables.

get_game_info only calls the API when the game was never fetched or its details are older than
max_age, since reviews and player counts change slowly. Details don't depend on the country."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import requests

INFO_URL = "https://api.isthereanydeal.com/games/info/v2"
# Review sources as ITAD names them in "reviews"
STEAM_REVIEWS = "Steam"
METASCORE = "Metascore"


def fetch_game_info(game_id: str, api_key: str) -> dict | None:
    """The API's details for the game, or None if ITAD doesn't know the id."""
    response = requests.get(INFO_URL, headers={"ITAD-API-Key": api_key}, params={"id": game_id}, timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def review(info: dict, source: str) -> dict:
    return next((r for r in info.get("reviews") or [] if r.get("source") == source), {})


def info_row(game_id: str, info: dict, fetched_at: str) -> tuple:
    """Turn an API response into a game_info row."""
    steam = review(info, STEAM_REVIEWS)
    players = info.get("players") or {}
    return (game_id, info["title"], info.get("appid"), info.get("releaseDate"),
            int(bool(info.get("earlyAccess"))), json.dumps(info.get("tags") or []),
            steam.get("score"), steam.get("count"), review(info, METASCORE).get("score"),
            players.get("recent"), players.get("peak"), fetched_at)


def company_rows(game_id: str, info: dict) -> list[tuple]:
    """Turn an API response into game_companies rows."""
    rows = set()  # a set, in case the API lists one company twice
    for role, field in (("publisher", "publishers"), ("developer", "developers")):
        for company in info.get(field) or []:
            rows.add((game_id, role, company["id"], company["name"]))
    return sorted(rows)


def read_game_info(conn: sqlite3.Connection, game_id: str) -> dict | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM game_info WHERE game_id = ?", (game_id,)).fetchone()
    if row is None:
        return None
    info = dict(row)
    info["early_access"] = bool(info["early_access"])
    info["tags"] = json.loads(info["tags"])
    companies = conn.execute(
        "SELECT role, company_id, company_name FROM game_companies WHERE game_id = ? ORDER BY company_id",
        (game_id,)
    ).fetchall()
    for role in ("publisher", "developer"):
        info[f"{role}s"] = [{"id": c["company_id"], "name": c["company_name"]} for c in companies if c["role"] == role]
    return info


def get_game_info(db_file: str, game_id: str, api_key: str, max_age: timedelta = timedelta(hours=168),
                  fetch=fetch_game_info, now: datetime | None = None) -> dict | None:
    """The game's cached details, fetching them from the API only when missing or stale.

    Returns None if ITAD doesn't know the game. Raises requests.RequestException if the API call
    fails; the database is left as it was."""
    now = now or datetime.now(timezone.utc)
    with sqlite3.connect(db_file) as conn:
        cached = read_game_info(conn, game_id)
    if cached and now - datetime.fromisoformat(cached["fetched_at"]) < max_age:
        return cached

    print(f"calling /info API for {game_id}")
    info = fetch(game_id, api_key)
    if info is None:
        return None
    # One transaction: replace the details and the company list together
    with sqlite3.connect(db_file) as conn:
        conn.execute("INSERT OR REPLACE INTO game_info VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     info_row(game_id, info, now.isoformat()))
        conn.execute("DELETE FROM game_companies WHERE game_id = ?", (game_id,))
        conn.executemany("INSERT INTO game_companies VALUES (?, ?, ?, ?)", company_rows(game_id, info))
    print(f"stored details for {info['title']} in game_info")

    with sqlite3.connect(db_file) as conn:
        return read_game_info(conn, game_id)
