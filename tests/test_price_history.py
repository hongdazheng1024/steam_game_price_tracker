import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
import requests

import steam_price_db_init
from price_history import FULL_HISTORY_SINCE, SYNC_OVERLAP, get_price_history

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def event(timestamp, price, regular=59.99, cut=0):
    return {"timestamp": timestamp, "shop": {"id": 61, "name": "Steam"},
            "deal": {"price": {"amount": price, "amountInt": int(price * 100), "currency": "USD"},
                     "regular": {"amount": regular, "amountInt": int(regular * 100), "currency": "USD"},
                     "cut": cut}}


class FakeApi:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, game_id, country, since, api_key):
        self.calls.append((game_id, country, since))
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


def history(db_file, api, now=NOW, game_id="game1", country="US"):
    return get_price_history(db_file, game_id, country, "key", fetch=api, now=now)


def test_first_call_fetches_full_history_and_returns_it_oldest_first(db_file):
    api = FakeApi([event("2026-06-01T10:00:00+02:00", 29.99, cut=50), event("2026-01-01T00:00:00+00:00", 59.99)])

    result = history(db_file, api)

    assert api.calls == [("game1", "US", FULL_HISTORY_SINCE)]
    assert result == [
        {"timestamp": "2026-01-01T00:00:00+00:00", "price": 59.99, "regular_price": 59.99, "cut": 0, "currency": "USD"},
        {"timestamp": "2026-06-01T08:00:00+00:00", "price": 29.99, "regular_price": 59.99, "cut": 50, "currency": "USD"},
    ]


def test_fresh_history_skips_the_api(db_file):
    api = FakeApi([event("2026-01-01T00:00:00+00:00", 59.99)])
    first = history(db_file, api)

    assert history(db_file, api, now=NOW + timedelta(hours=23)) == first
    assert len(api.calls) == 1


def test_stale_history_fetches_only_new_changes(db_file):
    old = event("2026-01-01T00:00:00+00:00", 59.99)
    new = event("2026-10-09T00:00:00+00:00", 39.99, cut=33)
    later = NOW + timedelta(hours=25)
    # The overlap means the refresh can return a change that's already stored
    api = FakeApi([old], [old, new])
    history(db_file, api)

    result = history(db_file, api, now=later)

    assert api.calls[1] == ("game1", "US", NOW - SYNC_OVERLAP)
    assert [row["price"] for row in result] == [59.99, 39.99]
    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT last_synced_at FROM price_history_sync").fetchall() == [(later.isoformat(),)]


def test_game_without_history_is_not_refetched_while_fresh(db_file):
    api = FakeApi([])

    assert history(db_file, api) == []
    assert history(db_file, api, now=NOW + timedelta(hours=1)) == []
    assert len(api.calls) == 1


def test_failed_fetch_leaves_the_database_unchanged(db_file):
    api = FakeApi(requests.ConnectionError("down"))

    with pytest.raises(requests.ConnectionError):
        history(db_file, api)

    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT COUNT(*) FROM price_history_sync").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM price_history").fetchone() == (0,)


def test_events_without_a_deal_are_skipped(db_file):
    api = FakeApi([{"timestamp": "2026-01-01T00:00:00+00:00", "shop": {"id": 61, "name": "Steam"}, "deal": None},
                   event("2026-02-01T00:00:00+00:00", 59.99)])

    assert [row["timestamp"] for row in history(db_file, api)] == ["2026-02-01T00:00:00+00:00"]


def test_countries_are_cached_separately(db_file):
    api = FakeApi([event("2026-01-01T00:00:00+00:00", 59.99)], [event("2026-01-01T00:00:00+00:00", 8980)])

    history(db_file, api, country="US")
    jp = history(db_file, api, country="JP")

    assert [call[1] for call in api.calls] == ["US", "JP"]
    assert [row["price"] for row in jp] == [8980]
