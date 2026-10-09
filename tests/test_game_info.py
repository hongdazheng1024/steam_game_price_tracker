import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
import requests

import steam_price_db_init
from game_info import get_game_info

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

# Trimmed from a real /games/info/v2 response for Monster Hunter Wilds
WILDS = {
    "id": "wilds", "title": "Monster Hunter Wilds", "appid": 2246340, "earlyAccess": False,
    "releaseDate": "2025-02-27", "tags": ["Hunting", "Action"],
    "developers": [{"id": 5, "name": "Capcom Co., Ltd."}, {"id": 34, "name": "Capcom"}],
    "publishers": [{"id": 5, "name": "Capcom Co., Ltd."}, {"id": 935, "name": "Capcom (JP)"}],
    "reviews": [{"score": 52, "source": "Steam", "count": 202561, "url": ""},
                {"score": 88, "source": "Metascore", "count": 43, "url": ""},
                {"score": 71, "source": "Metacritic User Score", "count": 1646, "url": ""}],
    "players": {"recent": 41744, "day": 43243, "week": 50476, "peak": 1384608},
}


class FakeApi:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, game_id, api_key):
        self.calls.append(game_id)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(steam_price_db_init, "DB_FILE", path)
    steam_price_db_init.init_db()
    return path


def info(db_file, api, now=NOW, game_id="wilds"):
    return get_game_info(db_file, game_id, "key", fetch=api, now=now)


def test_first_call_fetches_and_stores_details(db_file):
    api = FakeApi(WILDS)

    result = info(db_file, api)

    assert api.calls == ["wilds"]
    assert result == {
        "game_id": "wilds", "title": "Monster Hunter Wilds", "steam_appid": 2246340,
        "release_date": "2025-02-27", "early_access": False, "tags": ["Hunting", "Action"],
        "steam_score": 52, "steam_review_count": 202561, "metascore": 88,
        "players_recent": 41744, "players_peak": 1384608, "fetched_at": NOW.isoformat(),
        "publishers": [{"id": 5, "name": "Capcom Co., Ltd."}, {"id": 935, "name": "Capcom (JP)"}],
        "developers": [{"id": 5, "name": "Capcom Co., Ltd."}, {"id": 34, "name": "Capcom"}],
    }


def test_fresh_details_skip_the_api(db_file):
    api = FakeApi(WILDS)
    first = info(db_file, api)

    assert info(db_file, api, now=NOW + timedelta(days=6)) == first
    assert len(api.calls) == 1


def test_stale_details_are_replaced(db_file):
    updated = WILDS | {"reviews": [{"score": 60, "source": "Steam", "count": 250000, "url": ""}],
                       "publishers": [{"id": 5, "name": "Capcom Co., Ltd."}]}
    api = FakeApi(WILDS, updated)
    info(db_file, api)

    result = info(db_file, api, now=NOW + timedelta(days=8))

    assert len(api.calls) == 2
    assert (result["steam_score"], result["metascore"]) == (60, None)
    assert result["publishers"] == [{"id": 5, "name": "Capcom Co., Ltd."}]


def test_unknown_game_returns_none_and_stores_nothing(db_file):
    assert info(db_file, FakeApi(None)) is None
    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT COUNT(*) FROM game_info").fetchone() == (0,)


def test_missing_fields_are_stored_as_null(db_file):
    sparse = {"id": "new", "title": "New Game", "releaseDate": None, "reviews": [], "players": None}

    result = info(db_file, FakeApi(sparse), game_id="new")

    assert result["release_date"] is None and result["steam_score"] is None and result["players_peak"] is None
    assert (result["tags"], result["publishers"], result["early_access"]) == ([], [], False)


def test_failed_fetch_keeps_the_stale_details(db_file):
    api = FakeApi(WILDS, requests.ConnectionError("down"))
    first = info(db_file, api)

    with pytest.raises(requests.ConnectionError):
        info(db_file, api, now=NOW + timedelta(days=8))

    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT fetched_at FROM game_info").fetchall() == [(first["fetched_at"],)]
        assert conn.execute("SELECT COUNT(*) FROM game_companies").fetchone() == (4,)


def test_games_can_be_found_by_company_id(db_file):
    world = WILDS | {"id": "world", "title": "Monster Hunter: World",
                     "publishers": [{"id": 5, "name": "Capcom Co., Ltd."}, {"id": 5388, "name": "Capcom Japan Inc."}]}
    api = FakeApi(WILDS, world)
    info(db_file, api)
    info(db_file, api, game_id="world")

    with sqlite3.connect(db_file) as conn:
        rows = conn.execute(
            "SELECT game_id FROM game_companies WHERE company_id = 5 AND role = 'publisher' ORDER BY game_id"
        ).fetchall()
    assert rows == [("wilds",), ("world",)]
