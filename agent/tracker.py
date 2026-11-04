"""The price-tracking agent: Llama 3.1 with tools to find a game, look up its Steam price and predict
when it reaches a target price.

It keeps no conversation state. Each turn gets the whole conversation and the user's country, and
yields the reply as it's written (see chat_turn). api.py serves it over HTTP for the Spring Boot
backend, and gradio_app.py is a local chat UI on top of it."""
import difflib
import json
import logging
import os
import re
import sqlite3
import sys
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path

import requests
from openai import OpenAI

import steam_price_db_init
from comparables import TIER_COUNTRY, find_comparables
from game_info import get_game_info
from prediction import clean_intro, fallback_intro, needs_comparables, predict_target_price
from price_history import STEAM_SHOP_ID, get_price_history
from sale_calendar import load_sales
from steam_price_db_init import load_config

log = logging.getLogger(__name__)

RAG_DIR = Path(__file__).resolve().parent.parent / "RAG"
DB_FILE = steam_price_db_init.DB_FILE

COUNTRIES = ["US", "JP", "GB", "CA", "AU", "DE", "FR"]
# Llama 3.1 through Ollama's OpenAI-compatible API, unless the environment says otherwise
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.1")
ITAD_URL = "https://api.isthereanydeal.com"
ITAD_TIMEOUT = 30

db_config = load_config()
HISTORY_MAX_AGE = timedelta(hours=db_config["history_max_age_hours"])
GAME_INFO_MAX_AGE = timedelta(hours=db_config["game_info_max_age_hours"])
SALES = load_sales()


class MissingConfig(RuntimeError):
    pass


class LLMUnavailable(RuntimeError):
    pass


def itad_api_key() -> str:
    # ITAD-API-KEY is the .env name; ITAD_API_KEY is for shells and containers, which reject the hyphen
    key = os.getenv("ITAD_API_KEY") or os.getenv("ITAD-API-KEY")
    if not key:
        raise MissingConfig("ITAD_API_KEY was not found in the environment or the .env file.")
    return key


@cache
def llm_client() -> OpenAI:
    # Ollama doesn't check the key, but the client needs one
    return OpenAI(base_url=LLM_BASE_URL, api_key=os.getenv("MODEL_API_KEY", "ollama"))


def complete(**kwargs):
    try:
        return llm_client().chat.completions.create(model=LLM_MODEL, **kwargs)
    except Exception as e:
        # openai's connection and status errors; anything the model call raises means no reply
        raise LLMUnavailable(f"The model call failed: {e}") from e


@cache
def knowledge_base():
    # data_load lives in RAG/ and imports its neighbours directly, so RAG/ must be on the path.
    # Importing it loads the embedding model and opens (or rebuilds) the vector store, so it takes a moment.
    if str(RAG_DIR) not in sys.path:
        sys.path.insert(0, str(RAG_DIR))
    import data_load
    return data_load


SYSTEM_PROMPT = """You are a helpful gaming deal assistant that helps users track Steam game prices using an intelligent local caching system.

CRITICAL RULES FOR TOOL USE:
1. Call 'search_game' when the user names a video game, even if the title is partial or misspelled. Pass only the title.
2. Call 'find_game_candidates' when the user describes a game without naming it: by publisher or developer, genre, plot, or "the new X game". Pass the user's description as they wrote it.
3. Call 'predict_target_price' when the user asks when a game will reach a price, drop to a price, or get cheap enough for them. Pass the game title (from earlier in the conversation if the user doesn't repeat it) and their target price as a number. If they want to know when a game gets cheaper but haven't said what price they'd pay, ask them for it first and don't call any tool.
4. If the user says hello or asks a general question that isn't about a specific game, DO NOT call any tools. Instead, reply politely and ask them what game they want to look up.
5. Never guess, hallucinate, or make up a game title. Only use a title the user wrote or picked from a list.

EXAMPLES:
- "Is Hades on sale?" -> search_game(game_title="Hades")
- "how much is that Pragmata thing" -> search_game(game_title="Pragmata")
- "the new Capcom samurai game" -> find_game_candidates(description="the new Capcom samurai game")
- "that FromSoftware souls game" -> find_game_candidates(description="FromSoftware souls game")
- "Ubisoft's pirate game" -> find_game_candidates(description="Ubisoft's pirate game")
- "When will Monster Hunter Wilds drop to $25?" -> predict_target_price(game_title="Monster Hunter Wilds", target_price=25)
- (after talking about Hades) "will it hit 10 bucks soon?" -> predict_target_price(game_title="Hades", target_price=10)
- "when will Elden Ring get cheaper?" -> no tool; ask what price they'd like to pay
- "hi there" -> no tool

HOW TO HANDLE THE TOOL RESULT (check its "status" field):
- "match": the result has the game's price data. Summarize it as described below.
- "choices": the request matched more than one game, or (with "source": "rag") the description may mean any of these games. Show the "title" of each choice as a numbered list and ask which one they mean. Do not pick one yourself. When the user answers, call 'search_game' again with the exact title they picked.
- "not_found": with "source": "rag", no game matched the description: ask the user for the exact game title and don't call any tool until they give one. Otherwise, tell the user no game matched and ask them to check the title.
- "prediction": the result of 'predict_target_price'. Answer as described under HOW TO EXPLAIN A PREDICTION.
- "missing_target_price": ask the user what price they'd like to pay for the game.
- "error": tell the user the price lookup failed and to try again later.

HOW TO EXPLAIN A PREDICTION:
Reply with one or two short sentences that answer the user's question from the "headline", keeping its wording ("might", "is likely to", "is very likely to"). The user already sees every other detail, so don't add any numbers, dates, sales or details of your own, don't use lists or headings, and don't mention the tool or its result.

HOW TO INTEGRATE AND REASON WITH THE TOOL DATA:
1. The tool automatically checks a local SQL 'game_library' database first. If the record is less than 24 hours old, it returns cached data to save API calls. Otherwise, it updates with live data.
2. When summarizing the deal, do not mention database implementation details (like SQLite or tables) to the end user unless they explicitly ask how the application works.
3. Focus your summary on:
   - The current deal price vs the regular price (calculate the discount percentage if not already clear).
   - How close the current deal is to the historical lowest price (calculate the exact dollar difference to show value).
   - Provide a clear, analytical conclusion on whether they should buy now or wait for a better sale.
"""


# =====================================================================
# Prices and game lookup
# =====================================================================
def get_price_by_id(game_id: str, official_title: str, country: str) -> dict:
    # -------------------------------------------------------------------------
    # STEP 1: Check the Local Cache
    # -------------------------------------------------------------------------
    with sqlite3.connect(DB_FILE) as conn:
        row = conn.execute(
            "SELECT current_deal, historical_lowest, last_search_date FROM game_library WHERE id = ? AND country_code = ?",
            (game_id, country)
        ).fetchone()

    if row:
        cached_current, cached_lowest, last_search_str = row
        # Check if the cache record is fresh (less than 24 hours old)
        if datetime.now() - datetime.fromisoformat(last_search_str) < timedelta(hours=24):
            log.info("cache hit for '%s' (%s)", official_title, country)
            return {
                "title": official_title,
                "current_deal": json.loads(cached_current),
                "historical_lowest": json.loads(cached_lowest),
                "cached": True
            }

    # -------------------------------------------------------------------------
    # STEP 2: Cache Miss or Stale Data -> Query the API
    # -------------------------------------------------------------------------
    log.info("cache miss for '%s' (%s), calling /games/overview", official_title, country)
    try:
        # Steam only: without "shops", the current deal and the all-time low can come from any store
        overview_res = requests.post(f"{ITAD_URL}/games/overview/v2", headers={"ITAD-API-Key": itad_api_key()},
                                     params={"country": country, "shops": STEAM_SHOP_ID}, json=[game_id],
                                     timeout=ITAD_TIMEOUT)
        overview_res.raise_for_status()
        prices_list = overview_res.json().get("prices", [])
    except requests.RequestException as e:
        return {"error": f"Overview route failed: {str(e)}"}

    if not prices_list:
        return {"error": f"No pricing details available for {official_title}."}
    current_deal = prices_list[0].get("current")
    historical_lowest = prices_list[0].get("lowest")

    # -------------------------------------------------------------------------
    # STEP 3: Insert or Update the Database
    # -------------------------------------------------------------------------
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            INSERT INTO game_library (id, title, country_code, current_deal, historical_lowest, last_search_date)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id, country_code) DO UPDATE SET
                current_deal = excluded.current_deal,
                historical_lowest = excluded.historical_lowest,
                last_search_date = excluded.last_search_date;
        """, (game_id, official_title, country, json.dumps(current_deal), json.dumps(historical_lowest),
              datetime.now().isoformat()))
        conn.commit()

    return {
        "title": official_title,
        "current_deal": current_deal,
        "historical_lowest": historical_lowest,
        "cached": False
    }


def normalize_title(title: str) -> str:
    # Lowercase, turn punctuation into spaces and collapse whitespace, so "Clair Obscur: Expedition 33"
    # and "clair obscur expedition 33" compare equal. Used for both stored titles and user queries.
    return " ".join(re.sub(r"[\W_]+", " ", title.casefold()).split())


MAX_CHOICES = 10

# Every lookup below returns JSON with a "status" field:
#   "match"     -> price data for one game ({"id"} plus the fields of get_price_by_id)
#   "choices"   -> {"choices": [{"id", "title"}, ...], "source": "catalog" | "api" | "rag", "query"} for the user to pick from.
#                  With source "catalog" the list only covers games stored so far, so the UI should offer
#                  "None of these", which calls search_game again with search_online=True.
#                  With source "rag" the games come from a description, so "None of these" asks for the exact title.
#   "not_found" -> nothing matched the query ({"query"}, plus "source": "rag" for a description)
#   "error"     -> {"error": "..."}

def price_response(game_id: str, official_title: str, country: str) -> str:
    result = get_price_by_id(game_id, official_title, country)
    status = "error" if "error" in result else "match"
    return json.dumps({"status": status, "id": game_id, **result})


def choices_response(rows, source: str, query: str) -> str:
    return json.dumps({
        "status": "choices",
        "choices": [{"id": game_id, "title": title} for game_id, title in rows],
        "source": source,
        "query": query
    })


def current_price(game_id: str, country: str) -> dict:
    # For callers that already know the game id, like the backend's price-alert job.
    # The title comes from game_catalog, so the game must have been found through a search before.
    with sqlite3.connect(DB_FILE) as conn:
        row = conn.execute("SELECT official_title FROM game_catalog WHERE id = ?", (game_id,)).fetchone()
    if not row:
        return {"status": "error", "id": game_id, "error": f"'{game_id}' is not in the game catalog. Search for it first."}
    return json.loads(price_response(game_id, row[0], country))


def search_catalog(normalized_query: str) -> list:
    # Returns (id, official_title, is_exact) rows: exact title matches, or else titles containing the query
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute(
            "SELECT id, official_title FROM game_catalog WHERE normalized_title = ?",
            (normalized_query,)
        ).fetchall()
        if rows:
            return [(game_id, title, True) for game_id, title in rows]
        # normalize_title strips % and _, so the query can't add LIKE wildcards of its own
        rows = conn.execute(
            "SELECT id, official_title FROM game_catalog WHERE normalized_title LIKE ? ORDER BY length(official_title) LIMIT ?",
            (f"%{normalized_query}%", MAX_CHOICES)
        ).fetchall()
    return [(game_id, title, False) for game_id, title in rows]


def search_game(query: str, country: str = "US", search_online: bool = False) -> str:
    # search_online=True skips the local catalog, for when none of its choices were the right game
    normalized_query = normalize_title(query)
    if not normalized_query:
        return json.dumps({"status": "not_found", "query": query})

    # -------------------------------------------------------------------------
    # STEP 1: Look up the local catalog
    # -------------------------------------------------------------------------
    rows = []
    if not search_online:
        rows = search_catalog(normalized_query)
        # Exactly one exact match: no need to ask the user
        if len(rows) == 1 and rows[0][2]:
            game_id, official_title, _ = rows[0]
            log.info("exact match in game_catalog: %s", official_title)
            return price_response(game_id, official_title, country)

    # Same title for several games, or partial matches: let the user pick
    if rows:
        return choices_response([(game_id, title) for game_id, title, _ in rows], "catalog", query)

    # -------------------------------------------------------------------------
    # STEP 2: Nothing stored yet, or the user asked to search online -> search the ITAD API
    # -------------------------------------------------------------------------
    log.info("calling /games/search for '%s'", query)
    try:
        search_res = requests.get(f"{ITAD_URL}/games/search/v1", headers={"ITAD-API-Key": itad_api_key()},
                                  params={"title": query}, timeout=ITAD_TIMEOUT)
        search_res.raise_for_status()
        search_data = search_res.json()
    except requests.RequestException as e:
        log.warning("search route failed: %s", e)
        return json.dumps({"status": "error", "error": f"Search route failed: {str(e)}"})

    if not search_data:
        return json.dumps({"status": "not_found", "query": query})

    # -------------------------------------------------------------------------
    # STEP 3: Store every result in game_catalog for future lookups
    # -------------------------------------------------------------------------
    now_str = datetime.now().isoformat()
    with sqlite3.connect(DB_FILE) as conn:
        conn.executemany(
            """
            INSERT INTO game_catalog (id, official_title, normalized_title, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            [(game["id"], game["title"], normalize_title(game["title"]), now_str) for game in search_data]
        )
        conn.commit()

    # Exactly one result with the same title as the query: no need to ask the user
    exact = [game for game in search_data if normalize_title(game["title"]) == normalized_query]
    if len(exact) == 1:
        return price_response(exact[0]["id"], exact[0]["title"], country)

    return choices_response([(game["id"], game["title"]) for game in search_data[:MAX_CHOICES]], "api", query)


def itad_ids_for_steam_appids(games: list[dict]) -> dict:
    # games: [{"steam_appid", "title"}, ...] from the knowledge base.
    # Returns {steam_appid: (itad_id, official_title)}; games IsThereAnyDeal doesn't know are left out.
    appids = [game["steam_appid"] for game in games]
    placeholders = ",".join("?" * len(appids))
    query = f"SELECT steam_appid, id, official_title FROM game_catalog WHERE steam_appid IN ({placeholders})"
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute(query, appids).fetchall()
    found = {appid: (game_id, title) for appid, game_id, title in rows}

    missing = [game for game in games if game["steam_appid"] not in found]
    if not missing:
        return found

    # One request maps every missing Steam App ID (shop 61 is Steam) to its ITAD id
    log.info("calling /lookup for %d Steam App IDs", len(missing))
    try:
        lookup_res = requests.post(f"{ITAD_URL}/lookup/id/shop/{STEAM_SHOP_ID}/v1",
                                   headers={"ITAD-API-Key": itad_api_key()},
                                   json=[f"app/{game['steam_appid']}" for game in missing], timeout=ITAD_TIMEOUT)
        lookup_res.raise_for_status()
        lookup_data = lookup_res.json()
    except requests.RequestException as e:
        log.warning("lookup route failed: %s", e)
        return found

    # Store the mappings, keeping the title of games already in the catalog from an ITAD search
    now_str = datetime.now().isoformat()
    new_rows = [(lookup_data[f"app/{game['steam_appid']}"], game["title"], game["steam_appid"])
                for game in missing if lookup_data.get(f"app/{game['steam_appid']}")]
    with sqlite3.connect(DB_FILE) as conn:
        conn.executemany(
            """
            INSERT INTO game_catalog (id, official_title, normalized_title, created_at, steam_appid)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET steam_appid = excluded.steam_appid
            """,
            [(game_id, title, normalize_title(title), now_str, appid) for game_id, title, appid in new_rows]
        )
        conn.commit()
        rows = conn.execute(query, appids).fetchall()
    return {appid: (game_id, title) for appid, game_id, title in rows}


def find_games_by_description(description: str) -> str:
    # For "the new Capcom samurai game": the knowledge base finds the games, and the user always
    # picks one, even when there's only one candidate, since a description is never certain
    not_found = json.dumps({"status": "not_found", "query": description, "source": "rag"})
    candidates = [c for c in knowledge_base().find_game_candidates(description) if c["steam_appid"]]
    if not candidates:
        return not_found

    ids = itad_ids_for_steam_appids(candidates)
    rows = [ids[c["steam_appid"]] for c in candidates if c["steam_appid"] in ids]
    if not rows:
        return not_found
    return choices_response(rows, "rag", description)


def title_from_context(query: str, context: str):
    # The model copies the title from earlier in the conversation and sometimes misspells it
    # ("Monst Hunter Wilds"), so look for a close catalog title that the conversation mentions.
    # Returns (game_id, official_title), or None when no single game is close enough.
    padded_context = f" {normalize_title(context)} "
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("SELECT id, official_title, normalized_title FROM game_catalog").fetchall()
    mentioned = {}
    for game_id, title, normalized in rows:
        if f" {normalized} " in padded_context:
            mentioned.setdefault(normalized, []).append((game_id, title))
    close = difflib.get_close_matches(normalize_title(query), list(mentioned), n=1, cutoff=0.8)
    # Several games share that title: let search_game ask the user instead
    if not close or len(mentioned[close[0]]) > 1:
        return None
    return mentioned[close[0]][0]


def resolve_title(query: str, country: str, context: str = ""):
    # Returns ((game_id, official_title), None) for one clear match, or else (None, search_game's JSON
    # result: choices, not_found or error) to pass back to the model.
    # context is the conversation so far, for a title the model misspelled while copying it.
    rows = search_catalog(normalize_title(query))
    if len(rows) == 1 and rows[0][2]:
        return (rows[0][0], rows[0][1]), None
    found = title_from_context(query, context)
    if found:
        log.info("matched '%s' to '%s' from the conversation", query, found[1])
        return found, None
    # Without an exact match, search online: the catalog's partial matches may only be DLCs of the game
    result = search_game(query=query, country=country, search_online=True)
    if json.loads(result)["status"] == "match":
        # search_game found it online and stored it in game_catalog
        exact = [row for row in search_catalog(normalize_title(query)) if row[2]]
        if len(exact) == 1:
            return (exact[0][0], exact[0][1]), None
    return None, result


def parse_price(value) -> float | None:
    # The model may send 25, "25", "$25" or "25 USD"
    try:
        price = float(re.sub(r"[^\d.]", "", str(value)))
    except ValueError:
        return None
    return price if price > 0 else None


def predict_price(query: str, target_price: float, country: str, context: str = "") -> str:
    # Returns JSON with "status": "prediction" and the prediction's headline for the model to explain,
    # or search_game's result (choices, not_found, error) when the title doesn't name one game
    resolved, other = resolve_title(query, country, context)
    if other:
        return other
    game_id, official_title = resolved
    api_key = itad_api_key()
    try:
        history = get_price_history(DB_FILE, game_id, country, api_key, HISTORY_MAX_AGE)
        candidate_ids = []
        # Fetching comparables costs API calls, so only when the game's own history isn't enough.
        # The knowledge base's other games from the same publishers are the candidates.
        use_comparables = needs_comparables(history, target_price)
        if use_comparables:
            info = get_game_info(DB_FILE, game_id, api_key, GAME_INFO_MAX_AGE)
            kb_games = knowledge_base().games_by_companies([p["name"] for p in info["publishers"]]) if info else []
            if kb_games:
                candidate_ids = [itad_id for itad_id, _ in itad_ids_for_steam_appids(kb_games).values()]
        found = find_comparables(DB_FILE, game_id, api_key, candidate_ids,
                                 info_max_age=GAME_INFO_MAX_AGE, history_max_age=HISTORY_MAX_AGE)
        if found["target"] is None:
            return json.dumps({"status": "not_found", "query": query})
        # Mostly fetched by find_comparables already, so these come from the database
        comparable_histories = {c["game_id"]: get_price_history(DB_FILE, c["game_id"], TIER_COUNTRY, api_key,
                                                                HISTORY_MAX_AGE)
                                for c in found["comparables"]} if use_comparables else {}
    except requests.RequestException as e:
        log.warning("prediction failed: %s", e)
        return json.dumps({"status": "error", "error": f"Price history lookup failed: {str(e)}"})

    result = predict_target_price(found["target"], history, target_price, found["comparables"],
                                  comparable_histories, SALES, date.today())
    log.info("prediction for %s: %s, basis %s", official_title, result["status"], result["basis"])
    # "facts" is shown to the user as written; the model only sees the rest (see model_view)
    return json.dumps({"status": "prediction", "id": game_id, "title": official_title, "prediction_status": result["status"],
                       "confidence": result["confidence"], "headline": result["headline"], "facts": result["facts"]})


def model_view(tool_output: str) -> str:
    # What the model sees of a tool result: a prediction's facts block is left out, since finish_turn
    # shows it after the model's short intro. That keeps the model from rewording (and changing) it.
    result = json.loads(tool_output)
    if result.get("status") != "prediction":
        return tool_output
    return json.dumps({key: value for key, value in result.items() if key != "facts"})


# =====================================================================
# Tools
# =====================================================================
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_game",
            "description": "Looks up a video game by its title. Use it whenever the user names a game, even if the title is partial or misspelled. If exactly one game matches, returns its current deal, regular pricing and historical all-time lowest price in the selected country. If several games match, returns their titles so the user can choose one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "game_title": {
                        "type": "string",
                        "description": "The name of the video game (e.g., 'Cyberpunk 2077', 'Hades')."
                    }
                },
                "required": ["game_title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_game_candidates",
            "description": "Finds the games the user may mean when they describe a game without naming it: by publisher or developer, genre, plot, or 'the new X game'. Returns the possible games so the user can choose one. Don't use it when the user gives a title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "The user's description of the game (e.g., 'the new Capcom samurai game', 'Ubisoft's pirate game')."
                    }
                },
                "required": ["description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "predict_target_price",
            "description": "Predicts when a game's price will drop to the user's target price, from the game's price history and similar games from the same publisher, and when its regular price may be cut. Use it when the user asks when a game will reach, drop to or get down to a price.",
            "parameters": {
                "type": "object",
                "properties": {
                    "game_title": {
                        "type": "string",
                        "description": "The name of the video game, from the user's message or earlier in the conversation (e.g., 'Monster Hunter Wilds')."
                    },
                    "target_price": {
                        "type": "number",
                        "description": "The price the user wants to pay, as a number in their currency (e.g., 25 for $25)."
                    }
                },
                "required": ["game_title", "target_price"]
            }
        }
    }
]


def run_tool(tool_call, country: str, user_message: str = "", context: str = "") -> str:
    # The country is the user's setting, not something the model picks.
    # user_message is the user's latest message. A description is searched as the user wrote it, since
    # the model sometimes rewrites it or copies one from the prompt's examples.
    # context is the conversation so far, to correct a title the model copied from it with a typo.
    try:
        function_args = json.loads(tool_call.function.arguments)
    except json.JSONDecodeError:
        return json.dumps({"status": "error", "error": f"Invalid tool arguments: {tool_call.function.arguments}"})

    if tool_call.function.name == "search_game":
        game_title = function_args.get("game_title", "")
        result = search_game(query=game_title, country=country)
        # The model may have passed a description as a title, so try the knowledge base before giving up
        if json.loads(result)["status"] == "not_found":
            return find_games_by_description(user_message or game_title)
        return result
    if tool_call.function.name == "find_game_candidates":
        return find_games_by_description(user_message or function_args.get("description", ""))
    if tool_call.function.name == "predict_target_price":
        target_price = parse_price(function_args.get("target_price"))
        if target_price is None:
            return json.dumps({"status": "missing_target_price"})
        return predict_price(function_args.get("game_title", ""), target_price, country, context)
    return json.dumps({"status": "error", "error": f"Unknown tool '{tool_call.function.name}'."})


# =====================================================================
# Conversation turns
# =====================================================================
# A turn yields events as the reply is written:
#   {"type": "delta", "text": "..."}  -> the next piece of the reply's content; together the deltas are the content
#   {"type": "done", "reply": reply}  -> the finished reply, always last
# A reply is {"content": str, "game": None or {"id", "title"}, "choices": None or {"source", "query", "games": [{"id", "title"}, ...]}}.
# "game" is the one game the reply is about (a price or a prediction), so the client can offer to track it.
# With choices, the client shows each game as a button and sends the user's pick to select_option. With
# source "catalog" it also offers "None of these, search online", and with "rag" a plain "None of these".

ASK_FOR_TITLE = "Sorry I couldn't find it. What's the exact title of the game you're looking for?"
NONE_OF_THESE_MESSAGE = "None of these"
# Room for the one or two sentences the model writes in front of a prediction
PREDICTION_INTRO_TOKENS = 120


def text_reply(content: str, game: dict | None = None) -> Iterator[dict]:
    content = content or ""
    if content:
        yield {"type": "delta", "text": content}
    yield {"type": "done", "reply": {"content": content, "game": game, "choices": None}}


def choices_reply(result: dict) -> Iterator[dict]:
    if result["source"] == "rag":
        content = "🔍 These games may be the one you're describing. Which one do you mean?"
    else:
        content = f"🔍 I found several games matching '{result['query']}'. Which one do you mean?"
    yield {"type": "delta", "text": content}
    yield {"type": "done", "reply": {"content": content, "game": None, "choices": {
        "source": result["source"], "query": result["query"], "games": result["choices"]}}}


def finish_turn(messages: list, tool_outputs: list[str]) -> Iterator[dict]:
    results = [json.loads(output) for output in tool_outputs]
    # Several matching games: show them as choices instead of letting the model write (and renumber) the list
    for result in results:
        if result.get("status") == "choices":
            yield from choices_reply(result)
            return

    found = [result for result in results if result.get("status") in ("match", "prediction")]
    game = {"id": found[0]["id"], "title": found[0]["title"]} if found else None

    # A prediction only needs a sentence or two from the model, in front of the facts block.
    # The intro is checked as a whole (clean_intro), so it isn't streamed.
    predictions = [result for result in results if result.get("status") == "prediction"]
    if predictions:
        prediction = predictions[0]
        response = complete(messages=messages, max_tokens=PREDICTION_INTRO_TOKENS)
        # Trimmed to a sentence or two, and replaced when it adds details of its own
        intro = clean_intro(response.choices[0].message.content, prediction["headline"], prediction["confidence"])
        if intro is None:
            log.info("replacing the model's intro")
            intro = fallback_intro({"title": prediction["title"], "status": prediction["prediction_status"]})
        yield from text_reply(f"{intro}\n\n{prediction['facts']}", game)
        return

    # Second call: Feed the tool data back to Llama 3.1 so it can summarize, streamed since it's the slow part
    parts = []
    for chunk in complete(messages=messages, stream=True):
        text = chunk.choices[0].delta.content if chunk.choices else None
        if text:
            parts.append(text)
            yield {"type": "delta", "text": text}
    yield {"type": "done", "reply": {"content": "".join(parts), "game": game, "choices": None}}


def chat_turn(conversation: list[dict], country: str) -> Iterator[dict]:
    # conversation: {"role": "user" | "assistant", "content"} messages, oldest first, ending with the user's new message
    message = conversation[-1]["content"]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation
    # First call: Send prompt and tools schema to Llama 3.1
    response_message = complete(messages=messages, tools=TOOLS_SCHEMA).choices[0].message
    tool_calls = response_message.tool_calls
    if not tool_calls:
        yield from text_reply(response_message.content)
        return

    log.info("model called %s", ", ".join(call.function.name for call in tool_calls))
    messages.append(response_message)  # Llama's tool call intent
    context = "\n".join(m["content"] for m in conversation)
    tool_outputs = []
    for tool_call in tool_calls:
        tool_output = run_tool(tool_call, country, message, context)
        tool_outputs.append(tool_output)
        messages.append({
            "tool_call_id": tool_call.id,
            "role": "tool",
            "name": tool_call.function.name,
            "content": model_view(tool_output)
        })
    yield from finish_turn(messages, tool_outputs)


def selection_message(choice: dict) -> str:
    # The user message a pick stands for, to store in the conversation
    return choice["title"] if choice["type"] == "game" else NONE_OF_THESE_MESSAGE


def select_option(conversation: list[dict], choice: dict, country: str) -> Iterator[dict]:
    # The user picked one of a reply's choices. conversation is the conversation so far, ending with
    # the reply that had the choices; the pick is added as the user's message (see selection_message).
    # choice is one of:
    #   {"type": "game", "id", "title"}         -> that game's price
    #   {"type": "search_online", "query"}      -> search IsThereAnyDeal instead of the catalog
    #   {"type": "none_of_these"}               -> none of the games from a description: ask for the title
    if choice["type"] == "none_of_these":
        # No tool call or model reply needed
        yield from text_reply(ASK_FOR_TITLE)
        return
    if choice["type"] == "search_online":
        game_title = choice["query"]
        tool_output = search_game(query=game_title, country=country, search_online=True)
    else:
        game_title = choice["title"]
        tool_output = price_response(choice["id"], game_title, country)

    # Present the result to Llama 3.1 as a search_game call, the same way chat_turn does
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation + [
        {"role": "user", "content": selection_message(choice)},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "option_call",
                "type": "function",
                "function": {"name": "search_game", "arguments": json.dumps({"game_title": game_title})}
            }]
        },
        {"tool_call_id": "option_call", "role": "tool", "name": "search_game", "content": tool_output}
    ]
    yield from finish_turn(messages, [tool_output])


def final_reply(events: Iterator[dict]) -> dict:
    # The finished reply of a turn, for callers that don't stream
    for event in events:
        if event["type"] == "done":
            return event["reply"]
    raise RuntimeError("The turn ended without a reply.")
