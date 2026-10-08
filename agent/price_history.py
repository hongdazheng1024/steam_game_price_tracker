"""Steam price history from IsThereAnyDeal, cached in the price_history table.

get_price_history decides from price_history_sync whether to call the API:
  - never fetched     -> fetch the full history
  - fetched recently  -> no API call
  - fetched long ago  -> fetch only the changes since then
and then always reads the history from the database."""
import sqlite3
from datetime import datetime, timedelta, timezone

import requests

HISTORY_URL = "https://api.isthereanydeal.com/games/history/v2"
STEAM_SHOP_ID = 61
# Without `since` the API only returns the last 3 months, so the first fetch asks for everything after this
FULL_HISTORY_SINCE = datetime(2000, 1, 1, tzinfo=timezone.utc)
# A refresh starts a bit before the last sync, in case ITAD recorded a change late. Repeats are ignored.
SYNC_OVERLAP = timedelta(days=1)


def to_utc(timestamp: str) -> str:
    # ITAD timestamps carry their own offset ("2022-12-27T11:21:08+01:00"). Storing them all in UTC
    # keeps one change from being stored twice and makes text order the same as time order.
    return datetime.fromisoformat(timestamp).astimezone(timezone.utc).isoformat()


def fetch_history(game_id: str, country: str, since: datetime, api_key: str) -> list[dict]:
    response = requests.get(
        HISTORY_URL,
        headers={"ITAD-API-Key": api_key},
        params={"id": game_id, "country": country, "shops": STEAM_SHOP_ID, "since": since.isoformat()},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def history_rows(game_id: str, country: str, events: list[dict]) -> list[tuple]:
    """Turn API events into price_history rows."""
    rows = []
    for event in events:
        deal = event.get("deal")
        if not deal:  # no price, e.g. the game was delisted
            continue
        rows.append((game_id, country, event["shop"]["id"], to_utc(event["timestamp"]),
                     deal["price"]["amount"], deal["regular"]["amount"], deal["cut"],
                     deal["price"]["currency"]))
    return rows


def get_price_history(db_file: str, game_id: str, country: str, api_key: str,
                      max_age: timedelta = timedelta(hours=24), fetch=fetch_history,
                      now: datetime | None = None) -> list[dict]:
    """The game's Steam price changes, oldest first, fetching from the API only when needed.

    Raises requests.RequestException if the API call fails; the database is left as it was."""
    now = now or datetime.now(timezone.utc)
    with sqlite3.connect(db_file) as conn:
        row = conn.execute(
            "SELECT last_synced_at FROM price_history_sync WHERE game_id = ? AND country_code = ?",
            (game_id, country)
        ).fetchone()

    last_synced = datetime.fromisoformat(row[0]) if row else None
    if last_synced is None or now - last_synced >= max_age:
        since = FULL_HISTORY_SINCE if last_synced is None else last_synced - SYNC_OVERLAP
        print(f"calling /history API for {game_id} ({country}) since {since.date()}")
        rows = history_rows(game_id, country, fetch(game_id, country, since, api_key))
        # One transaction: the sync time is only saved together with the rows it covers
        with sqlite3.connect(db_file) as conn:
            conn.executemany("INSERT OR IGNORE INTO price_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
            conn.execute(
                """
                INSERT INTO price_history_sync (game_id, country_code, last_synced_at) VALUES (?, ?, ?)
                ON CONFLICT(game_id, country_code) DO UPDATE SET last_synced_at = excluded.last_synced_at
                """,
                (game_id, country, now.isoformat())
            )
        print(f"stored {len(rows)} price changes in price_history")

    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT timestamp, price, regular_price, cut, currency FROM price_history
            WHERE game_id = ? AND country_code = ? ORDER BY timestamp
            """,
            (game_id, country)
        ).fetchall()
    return [dict(row) for row in rows]
