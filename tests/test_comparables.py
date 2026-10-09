import pytest
import requests

import comparables
import game_info
import price_history
import steam_price_db_init
from comparables import price_tier, rank_comparables, reception

CAPCOM = {"id": 5, "name": "Capcom"}
EA = {"id": 7, "name": "Electronic Arts"}


def game(game_id, publisher=CAPCOM, launch_price=59.99, release_date="2020-01-01", tags=("Action",),
         steam_score=85):
    return {"game_id": game_id, "title": game_id.title(), "release_date": release_date,
            "launch_price": launch_price, "steam_score": steam_score, "tags": list(tags),
            "publishers": [publisher]}


TARGET = game("target", release_date="2025-02-27", tags=("Action", "Hunting"))


def ids(ranked):
    return [c["game_id"] for c in ranked]


@pytest.mark.parametrize("price, tier", [(9.99, "under $15"), (14.99, "under $15"), (19.99, "$15-30"),
                                         (29.99, "$15-30"), (49.99, "$30-50"), (59.99, "$50+"),
                                         (69.99, "$50+"), (None, None)])
def test_price_tier(price, tier):
    assert price_tier(price) == tier


@pytest.mark.parametrize("score, label", [(95, "positive"), (80, "positive"), (79, "mostly positive"),
                                          (40, "mixed"), (39, "negative"), (None, None)])
def test_reception(score, label):
    assert reception(score) == label


def test_ladder_order():
    candidates = [
        game("other_publisher_same_tier", publisher=EA),
        game("same_publisher_other_tier", launch_price=19.99),
        game("same_publisher_same_tier"),
        game("other_publisher_no_shared_tag", publisher=EA, tags=("Puzzle",)),
        game("other_publisher_other_tier", publisher=EA, launch_price=19.99),
    ]

    ranked = rank_comparables(TARGET, candidates)

    assert ids(ranked) == ["same_publisher_same_tier", "same_publisher_other_tier", "other_publisher_same_tier"]
    assert [c["match"] for c in ranked] == ["same publisher and price tier", "same publisher",
                                            "same price tier and genre"]


def test_only_games_released_before_the_target_count():
    candidates = [game("target", release_date="2020-01-01"), game("newer", release_date="2025-06-01"),
                  game("same_day", release_date="2025-02-27"), game("no_date", release_date=None),
                  game("older")]

    assert ids(rank_comparables(TARGET, candidates)) == ["older"]


def test_unreleased_target_compares_with_any_released_game():
    unreleased = TARGET | {"release_date": None}
    assert ids(rank_comparables(unreleased, [game("recent", release_date="2026-09-01")])) == ["recent"]


def test_within_a_level_same_reception_then_shared_tags_then_newest():
    candidates = [
        game("old_two_tags", tags=("Action", "Hunting"), release_date="2015-01-01"),
        game("new_two_tags", tags=("Action", "Hunting"), release_date="2018-01-01"),
        game("one_tag", tags=("Action",), release_date="2024-01-01"),
        game("negative_reception", tags=("Action", "Hunting"), steam_score=30, release_date="2024-06-01"),
    ]

    assert ids(rank_comparables(TARGET, candidates)) == ["new_two_tags", "old_two_tags", "one_tag",
                                                         "negative_reception"]


def test_unknown_launch_price_still_matches_same_publisher():
    candidates = [game("capcom_unknown", launch_price=None), game("ea_unknown", publisher=EA, launch_price=None)]

    ranked = rank_comparables(TARGET, candidates)

    assert ids(ranked) == ["capcom_unknown"]
    assert (ranked[0]["level"], ranked[0]["price_tier"]) == (2, None)


def test_max_results():
    candidates = [game(f"game{i}", release_date=f"201{i}-01-01") for i in range(8)]
    assert ids(rank_comparables(TARGET, candidates, max_results=3)) == ["game7", "game6", "game5"]


# --- find_comparables, with the API faked ---

def info_response(game_id, publisher=CAPCOM, release_date="2020-01-01", tags=("Action",), steam_score=85):
    return {"id": game_id, "title": game_id.title(), "releaseDate": release_date, "tags": list(tags),
            "publishers": [publisher], "developers": [],
            "reviews": [{"source": "Steam", "score": steam_score, "count": 1000}]}


def price_event(price, timestamp="2020-01-01T00:00:00+00:00"):
    return {"timestamp": timestamp, "shop": {"id": 61, "name": "Steam"},
            "deal": {"price": {"amount": price, "currency": "USD"},
                     "regular": {"amount": price, "currency": "USD"}, "cut": 0}}


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A fake ITAD: set api.infos[game_id] and api.histories[game_id] to what the API returns.

    A missing info means ITAD doesn't know the game; an exception is raised as an API failure."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(steam_price_db_init, "DB_FILE", db_file)
    steam_price_db_init.init_db()

    class Api:
        infos, histories, calls = {}, {}, []
        db = db_file

    def fake_info(game_id, api_key):
        Api.calls.append(("info", game_id))
        response = Api.infos.get(game_id)
        if isinstance(response, Exception):
            raise response
        return response

    def fake_history(game_id, country, since, api_key):
        Api.calls.append(("history", game_id, country))
        return Api.histories.get(game_id, [])

    monkeypatch.setattr(comparables, "get_game_info", lambda db, gid, key, max_age:
                        game_info.get_game_info(db, gid, key, max_age, fetch=fake_info))
    monkeypatch.setattr(comparables, "get_price_history", lambda db, gid, country, key, max_age:
                        price_history.get_price_history(db, gid, country, key, max_age, fetch=fake_history))
    return Api


def find(api, game_id="target", candidate_ids=()):
    return comparables.find_comparables(api.db, game_id, "key", candidate_ids)


def test_finds_comparables_from_candidates_and_cached_games(api):
    api.infos = {"target": info_response("target", release_date="2025-02-27"),
                 "world": info_response("world", release_date="2018-08-08"),
                 "cached": info_response("cached", publisher=EA, release_date="2019-01-01")}
    api.histories = {"target": [price_event(69.99, "2025-02-27T00:00:00+00:00")],
                     "world": [price_event(29.99, "2020-01-01T00:00:00+00:00"),
                               price_event(59.99, "2018-08-08T00:00:00+00:00")],
                     "cached": [price_event(59.99)]}
    find(api, game_id="cached")  # an earlier lookup leaves "cached" in the database

    result = find(api, candidate_ids=["world"])

    assert result["target"] == {"game_id": "target", "title": "Target", "release_date": "2025-02-27",
                                "release_date_estimated": False, "launch_price": 69.99, "price_tier": "$50+", "steam_score": 85,
                                "reception": "positive"}
    # World's launch price is its first regular price, not its later cut
    assert [(c["game_id"], c["launch_price"], c["level"]) for c in result["comparables"]] == \
        [("world", 59.99, 1), ("cached", 59.99, 3)]


def test_missing_release_date_falls_back_to_first_price(api):
    api.infos = {"target": info_response("target", release_date="2025-02-27"),
                 "undated": info_response("undated", release_date=None)}
    api.histories = {"undated": [price_event(59.99, "2015-06-17T04:16:59+00:00")]}

    [comparable] = find(api, candidate_ids=["undated"])["comparables"]

    assert (comparable["release_date"], comparable["release_date_estimated"]) == ("2015-06-17", True)


def test_tiers_use_the_us_history(api):
    api.infos = {"target": info_response("target", release_date="2025-02-27")}
    api.histories = {"target": [price_event(69.99)]}

    find(api)

    assert ("history", "target", "US") in api.calls


def test_unknown_target(api):
    assert find(api) == {"target": None, "comparables": []}


def test_failing_or_unknown_candidates_are_skipped(api):
    api.infos = {"target": info_response("target", release_date="2025-02-27"),
                 "broken": requests.ConnectionError("down"),
                 "good": info_response("good")}

    result = find(api, candidate_ids=["broken", "unknown", "good"])

    assert [c["game_id"] for c in result["comparables"]] == ["good"]


def test_candidate_fetches_are_capped(api):
    api.infos = {"target": info_response("target", release_date="2025-02-27")}
    candidate_ids = [f"game{i}" for i in range(comparables.MAX_FETCHED_CANDIDATES + 5)]
    api.infos |= {cid: info_response(cid) for cid in candidate_ids}

    find(api, candidate_ids=candidate_ids + ["target"])

    fetched = {call[1] for call in api.calls if call[0] == "info"} - {"target"}
    assert len(fetched) == comparables.MAX_FETCHED_CANDIDATES
