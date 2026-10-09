"""Games comparable to a target game, for predicting its prices when its own history is thin.

Comparables come from a ladder, best first:
  1. same publisher and same launch price tier
  2. same publisher, other or unknown tier (one publisher may discount a AAA and an indie game differently)
  3. another publisher, same tier and at least one shared tag
Within a level, games with the same Steam reception come first, then more shared tags, then newer games.
Only games released before the target count, since their history must cover the target's age."""
import sqlite3
from datetime import timedelta

import requests

from game_info import get_game_info, read_game_info
from price_history import get_price_history

# Upper bounds in US dollars: 19.99 is "$15-30", 59.99 is "$50+"
PRICE_TIERS = [(15, "under $15"), (30, "$15-30"), (50, "$30-50"), (float("inf"), "$50+")]
# Steam's own review labels, by percent of positive reviews
RECEPTIONS = [(80, "positive"), (70, "mostly positive"), (40, "mixed"), (0, "negative")]
MATCHES = {1: "same publisher and price tier", 2: "same publisher", 3: "same price tier and genre"}
# Tiers are in dollars, so launch prices always come from the US history, whatever the user's country
TIER_COUNTRY = "US"
# Each candidate fetched for the first time costs two API calls (details and history)
MAX_FETCHED_CANDIDATES = 10


def price_tier(launch_price: float | None) -> str | None:
    if launch_price is None:
        return None
    return next(label for bound, label in PRICE_TIERS if launch_price < bound)


def reception(steam_score: int | None) -> str | None:
    if steam_score is None:
        return None
    return next(label for bound, label in RECEPTIONS if steam_score >= bound)


def describe(game: dict) -> dict:
    """The fields a prediction needs, from get_game_info's details plus "launch_price"."""
    return {"game_id": game["game_id"], "title": game["title"], "release_date": game["release_date"],
            "release_date_estimated": game.get("release_date_estimated", False), "launch_price": game["launch_price"], "price_tier": price_tier(game["launch_price"]),
            "steam_score": game["steam_score"], "reception": reception(game["steam_score"])}


def match_level(target: dict, candidate: dict, shared_tags: list) -> int | None:
    same_publisher = bool({p["id"] for p in target["publishers"]} & {p["id"] for p in candidate["publishers"]})
    tier = price_tier(target["launch_price"])
    same_tier = tier is not None and tier == price_tier(candidate["launch_price"])
    if same_publisher:
        return 1 if same_tier else 2
    if same_tier and shared_tags:
        return 3
    return None


def rank_comparables(target: dict, candidates: list[dict], max_results: int = 5) -> list[dict]:
    """The best comparables for target, best first.

    target and candidates are get_game_info details plus "launch_price" (None if unknown)."""
    target_tags = set(target["tags"])
    target_reception = reception(target["steam_score"])
    ranked = []
    for candidate in candidates:
        if candidate["game_id"] == target["game_id"] or not candidate["release_date"]:
            continue
        # An unreleased target (no date) can be compared with any released game
        if target["release_date"] and candidate["release_date"] >= target["release_date"]:
            continue
        shared_tags = [tag for tag in candidate["tags"] if tag in target_tags]
        level = match_level(target, candidate, shared_tags)
        if level is not None:
            ranked.append(describe(candidate) | {"level": level, "match": MATCHES[level], "shared_tags": shared_tags})

    # Newest first, then the stable sort keeps that order among otherwise equal games
    ranked.sort(key=lambda c: c["release_date"], reverse=True)
    ranked.sort(key=lambda c: (c["level"], c["reception"] != target_reception, -len(c["shared_tags"])))
    return ranked[:max_results]


def with_first_price(conn: sqlite3.Connection, info: dict) -> dict:
    """info plus "launch_price": the first regular price on record (None without history).

    For games ITAD started tracking after release that's an approximation, but still a good sign of
    the game's scale. A game without a release date gets the date of its first price instead, which
    may be a pre-order date, marked with "release_date_estimated"."""
    row = conn.execute(
        """
        SELECT timestamp, regular_price FROM price_history WHERE game_id = ? AND country_code = ?
        ORDER BY timestamp LIMIT 1
        """,
        (info["game_id"], TIER_COUNTRY)
    ).fetchone()
    info = info | {"launch_price": row[1] if row else None}
    if not info["release_date"] and row:
        info |= {"release_date": row[0][:10], "release_date_estimated": True}
    return info


def fetch_candidate(db_file: str, game_id: str, api_key: str, info_max_age: timedelta,
                    history_max_age: timedelta) -> bool:
    """Cache the game's details and US history. False if ITAD doesn't know it or the API failed."""
    try:
        if get_game_info(db_file, game_id, api_key, info_max_age) is None:
            return False
        get_price_history(db_file, game_id, TIER_COUNTRY, api_key, history_max_age)
        return True
    except requests.RequestException as e:
        print(f"skipping comparable {game_id}: {e}")
        return False


def find_comparables(db_file: str, game_id: str, api_key: str, candidate_ids: list[str] = (),
                     max_results: int = 5, info_max_age: timedelta = timedelta(hours=168),
                     history_max_age: timedelta = timedelta(hours=24)) -> dict:
    """{"target": ..., "comparables": [...]} for the game, or {"target": None, ...} if ITAD doesn't know it.

    candidate_ids are games worth fetching, e.g. the knowledge base's games from the same publisher;
    at most MAX_FETCHED_CANDIDATES of them are used. Games whose details and US history are already
    cached are considered too, at no API cost. Raises requests.RequestException if the target's
    details or history can't be fetched."""
    if get_game_info(db_file, game_id, api_key, info_max_age) is None:
        return {"target": None, "comparables": []}
    get_price_history(db_file, game_id, TIER_COUNTRY, api_key, history_max_age)

    fresh_ids = [cid for cid in dict.fromkeys(candidate_ids) if cid != game_id][:MAX_FETCHED_CANDIDATES]
    for cid in fresh_ids:
        fetch_candidate(db_file, cid, api_key, info_max_age, history_max_age)

    with sqlite3.connect(db_file) as conn:
        cached_ids = [row[0] for row in conn.execute(
            """
            SELECT game_id FROM game_info
            WHERE game_id IN (SELECT game_id FROM price_history_sync WHERE country_code = ?)
            """,
            (TIER_COUNTRY,)
        )]
        games = {}
        for gid in cached_ids:
            games[gid] = with_first_price(conn, read_game_info(conn, gid))

    target = games.pop(game_id)
    return {"target": describe(target),
            "comparables": rank_comparables(target, list(games.values()), max_results)}
