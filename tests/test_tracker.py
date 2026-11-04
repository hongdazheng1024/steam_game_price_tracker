import json
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import requests

import steam_price_db_init
import tracker


def message(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


def tool_call(name, **arguments):
    return SimpleNamespace(id=f"call_{name}", function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def stream(*parts):
    return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=part))]) for part in parts])


class FakeLLM:
    """Stands in for llm_client(): returns the scripted responses in order and records each call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


@pytest.fixture
def llm(monkeypatch):
    def install(*responses):
        fake = FakeLLM(*responses)
        monkeypatch.setattr(tracker, "llm_client", lambda: fake)
        return fake
    return install


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(steam_price_db_init, "DB_FILE", path)
    monkeypatch.setattr(tracker, "DB_FILE", path)
    steam_price_db_init.init_db()
    return path


def events_of(events):
    events = list(events)
    deltas = "".join(event["text"] for event in events if event["type"] == "delta")
    assert events[-1]["type"] == "done"
    assert deltas == events[-1]["reply"]["content"]  # the deltas always add up to the reply
    return events


HADES = {"status": "match", "id": "hades", "title": "Hades", "current_deal": {"price": {"amount": 12.49}},
         "historical_lowest": {"price": {"amount": 9.99}}, "cached": True}
CHOICES = {"status": "choices", "choices": [{"id": "prey-1", "title": "Prey"}, {"id": "prey-2", "title": "Prey"}],
           "source": "api", "query": "prey"}


def test_reply_without_tools(llm):
    fake = llm(message("Hi! Which game should I look up?"))
    events = events_of(tracker.chat_turn([{"role": "user", "content": "hi"}], "US"))
    assert events[-1]["reply"] == {"content": "Hi! Which game should I look up?", "game": None, "choices": None}
    assert fake.calls[0]["tools"] == tracker.TOOLS_SCHEMA
    assert fake.calls[0]["messages"][0] == {"role": "system", "content": tracker.SYSTEM_PROMPT}


def test_price_summary_is_streamed_and_uses_the_users_country(llm, monkeypatch):
    searches = []
    monkeypatch.setattr(tracker, "search_game", lambda query, country: searches.append((query, country))
                        or json.dumps(HADES))
    fake = llm(message(tool_calls=[tool_call("search_game", game_title="Hades")]), stream("Buy ", "now."))

    events = events_of(tracker.chat_turn([{"role": "user", "content": "Is Hades on sale?"}], "GB"))

    assert searches == [("Hades", "GB")]
    assert [event["text"] for event in events if event["type"] == "delta"] == ["Buy ", "now."]
    assert events[-1]["reply"] == {"content": "Buy now.", "game": {"id": "hades", "title": "Hades"}, "choices": None}
    summary_call = fake.calls[1]
    assert summary_call["stream"] is True
    assert summary_call["messages"][-1] == {"tool_call_id": "call_search_game", "role": "tool",
                                            "name": "search_game", "content": json.dumps(HADES)}


def test_choices_are_returned_without_a_second_model_call(llm, monkeypatch):
    monkeypatch.setattr(tracker, "search_game", lambda query, country: json.dumps(CHOICES))
    fake = llm(message(tool_calls=[tool_call("search_game", game_title="prey")]))

    reply = tracker.final_reply(tracker.chat_turn([{"role": "user", "content": "prey price"}], "US"))

    assert len(fake.calls) == 1
    assert reply["choices"] == {"source": "api", "query": "prey", "games": CHOICES["choices"]}
    assert reply["game"] is None
    assert "prey" in reply["content"]


def test_a_title_that_finds_nothing_is_searched_as_a_description(llm, monkeypatch):
    monkeypatch.setattr(tracker, "search_game", lambda query, country: json.dumps({"status": "not_found"}))
    descriptions = []
    monkeypatch.setattr(tracker, "find_games_by_description",
                        lambda description: descriptions.append(description) or json.dumps(
                            {**CHOICES, "source": "rag", "query": description}))
    llm(message(tool_calls=[tool_call("search_game", game_title="Capcom samurai")]))

    reply = tracker.final_reply(tracker.chat_turn([{"role": "user", "content": "the new Capcom samurai game"}], "US"))

    assert descriptions == ["the new Capcom samurai game"]
    assert reply["choices"]["source"] == "rag"


def test_prediction_reply_is_the_intro_and_the_facts_block(llm, monkeypatch):
    prediction = {"status": "prediction", "id": "hades", "title": "Hades", "prediction_status": "predicted",
                  "confidence": "medium", "headline": "Hades is likely to reach $10.00 around Dec 2026.",
                  "facts": "**Hades → $10.00**"}
    monkeypatch.setattr(tracker, "predict_price", lambda query, target, country, context: json.dumps(prediction))
    fake = llm(message(tool_calls=[tool_call("predict_target_price", game_title="Hades", target_price=10)]),
               message("Hades is likely to reach $10.00 around Dec 2026."))

    events = events_of(tracker.chat_turn([{"role": "user", "content": "when will Hades hit $10?"}], "US"))

    reply = events[-1]["reply"]
    assert reply["content"] == "Hades is likely to reach $10.00 around Dec 2026.\n\n**Hades → $10.00**"
    assert reply["game"] == {"id": "hades", "title": "Hades"}
    assert fake.calls[1]["max_tokens"] == tracker.PREDICTION_INTRO_TOKENS
    # The model never sees the facts block
    assert "facts" not in json.loads(fake.calls[1]["messages"][-1]["content"])


def test_missing_target_price_goes_back_to_the_model(llm):
    fake = llm(message(tool_calls=[tool_call("predict_target_price", game_title="Hades", target_price=0)]),
               stream("What price would you pay?"))
    reply = tracker.final_reply(tracker.chat_turn([{"role": "user", "content": "when will Hades drop?"}], "US"))
    assert reply["content"] == "What price would you pay?"
    assert json.loads(fake.calls[1]["messages"][-1]["content"]) == {"status": "missing_target_price"}


def test_picking_a_game_looks_up_its_price(llm, monkeypatch):
    lookups = []
    monkeypatch.setattr(tracker, "price_response", lambda game_id, title, country: lookups.append(
        (game_id, title, country)) or json.dumps(HADES))
    fake = llm(stream("Good deal."))
    history = [{"role": "user", "content": "hades"}, {"role": "assistant", "content": "Which one do you mean?"}]
    choice = {"type": "game", "id": "hades", "title": "Hades"}

    reply = tracker.final_reply(tracker.select_option(history, choice, "JP"))

    assert lookups == [("hades", "Hades", "JP")]
    assert reply == {"content": "Good deal.", "game": {"id": "hades", "title": "Hades"}, "choices": None}
    assert tracker.selection_message(choice) == "Hades"
    sent = fake.calls[0]["messages"]
    assert sent[1:3] == history
    assert sent[3] == {"role": "user", "content": "Hades"}


def test_none_of_these_asks_for_the_title_without_the_model(llm):
    fake = llm()
    choice = {"type": "none_of_these"}
    reply = tracker.final_reply(tracker.select_option([], choice, "US"))
    assert reply["content"] == tracker.ASK_FOR_TITLE
    assert fake.calls == []
    assert tracker.selection_message(choice) == "None of these"


def test_search_online_skips_the_catalog(llm, monkeypatch):
    searches = []
    monkeypatch.setattr(tracker, "search_game", lambda query, country, search_online: searches.append(
        (query, country, search_online)) or json.dumps(CHOICES))
    llm()
    reply = tracker.final_reply(tracker.select_option([], {"type": "search_online", "query": "prey"}, "US"))
    assert searches == [("prey", "US", True)]
    assert reply["choices"]["source"] == "api"


def test_model_errors_become_llm_unavailable(monkeypatch):
    def fail(**kwargs):
        raise ConnectionError("connection refused")
    monkeypatch.setattr(tracker, "llm_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fail))))
    with pytest.raises(tracker.LLMUnavailable):
        list(tracker.chat_turn([{"role": "user", "content": "hi"}], "US"))


def add_to_catalog(db_file, *games):
    with sqlite3.connect(db_file) as conn:
        conn.executemany("INSERT INTO game_catalog (id, official_title, normalized_title, created_at) VALUES (?, ?, ?, ?)",
                         [(game_id, title, title.lower(), "2026-10-01T00:00:00") for game_id, title in games])


def overview(game_id, amount=12.49, lowest=9.99):
    return {"id": game_id, "current": {"price": {"amount": amount}},
            "lowest": {"price": {"amount": lowest}} if lowest else None, "bundled": 0, "urls": {}}


class FakeOverview:
    """Stands in for requests.post to /games/overview/v2."""

    def __init__(self, *entries, error=None):
        self.entries = entries
        self.error = error
        self.calls = []

    def __call__(self, url, headers, params, json, timeout):
        self.calls.append({"url": url, "params": params, "ids": json})
        if self.error:
            raise self.error
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"prices": list(self.entries), "bundles": []})


@pytest.fixture
def itad(monkeypatch):
    monkeypatch.setenv("ITAD_API_KEY", "key")

    def install(*entries, error=None):
        fake = FakeOverview(*entries, error=error)
        monkeypatch.setattr(tracker.requests, "post", fake)
        return fake
    return install


def test_current_prices_makes_one_call_and_matches_results_by_id(db_file, itad):
    add_to_catalog(db_file, ("a", "Game A"), ("b", "Game B"), ("c", "Game C"))
    # ITAD returns its own order and leaves out games with no Steam price
    fake = itad(overview("c", amount=3, lowest=None), overview("a", amount=1))

    results = tracker.current_prices(["a", "b", "c"], "GB")

    assert len(fake.calls) == 1
    assert fake.calls[0]["ids"] == ["a", "b", "c"]
    assert fake.calls[0]["params"] == {"country": "GB", "shops": tracker.STEAM_SHOP_ID}
    assert [(r["id"], r["status"]) for r in results] == [("a", "match"), ("b", "no_price"), ("c", "match")]
    assert results[0]["current_deal"] == {"price": {"amount": 1}}
    assert results[0]["title"] == "Game A"
    assert results[0]["cached"] is False
    assert results[1] == {"status": "no_price", "id": "b", "title": "Game B"}
    assert results[2]["historical_lowest"] is None


def test_current_prices_only_fetches_what_isnt_cached(db_file, itad):
    add_to_catalog(db_file, ("a", "Game A"), ("b", "Game B"))
    itad(overview("a"))
    tracker.current_prices(["a"], "US")

    fake = itad(overview("b"))
    results = tracker.current_prices(["a", "b"], "US")

    assert fake.calls[0]["ids"] == ["b"]
    assert [r["cached"] for r in results] == [True, False]
    assert results[0]["current_deal"] == {"price": {"amount": 12.49}}


def test_cache_is_per_country_and_expires(db_file, itad):
    add_to_catalog(db_file, ("a", "Game A"))
    itad(overview("a"))
    tracker.current_prices(["a"], "US")

    fake = itad(overview("a"))
    assert tracker.current_prices(["a"], "JP")[0]["cached"] is False
    assert len(fake.calls) == 1

    with sqlite3.connect(db_file) as conn:
        conn.execute("UPDATE game_library SET last_search_date = ?",
                     ((datetime.now() - timedelta(hours=25)).isoformat(),))
    assert tracker.current_prices(["a"], "US")[0]["cached"] is False
    assert len(fake.calls) == 2


def age_cached_prices(db_file, hours):
    with sqlite3.connect(db_file) as conn:
        conn.execute("UPDATE game_library SET last_search_date = ?",
                     ((datetime.now() - timedelta(hours=hours)).isoformat(),))


def test_max_age_refetches_older_prices_and_refreshes_the_cache(db_file, itad):
    add_to_catalog(db_file, ("a", "Game A"))
    itad(overview("a", amount=20))
    tracker.current_prices(["a"], "US")
    age_cached_prices(db_file, 7)

    # Still fresh enough for the default, so chat wouldn't call the API
    fake = itad(overview("a", amount=5))
    assert tracker.current_prices(["a"], "US")[0]["cached"] is True
    assert tracker.current_prices(["a"], "US", timedelta(hours=8))[0]["cached"] is True
    assert fake.calls == []

    fresh = tracker.current_prices(["a"], "US", timedelta(hours=6))[0]
    assert fresh["cached"] is False
    assert fresh["current_deal"] == {"price": {"amount": 5}}
    assert len(fake.calls) == 1
    # The fetched price is what later lookups get
    assert tracker.current_prices(["a"], "US")[0]["current_deal"] == {"price": {"amount": 5}}


def test_max_age_only_refetches_the_prices_that_are_too_old(db_file, itad):
    add_to_catalog(db_file, ("a", "Game A"), ("b", "Game B"))
    itad(overview("a"))
    tracker.current_prices(["a"], "US")
    age_cached_prices(db_file, 7)
    itad(overview("b"))
    tracker.current_prices(["b"], "US")

    fake = itad(overview("a"))
    results = tracker.current_prices(["a", "b"], "US", timedelta(hours=6))

    assert fake.calls[0]["ids"] == ["a"]
    assert [r["cached"] for r in results] == [False, True]


def test_max_age_zero_fetches_every_price(db_file, itad):
    add_to_catalog(db_file, ("a", "Game A"))
    itad(overview("a"))
    tracker.current_prices(["a"], "US")
    fake = itad(overview("a"))
    assert tracker.current_prices(["a"], "US", timedelta(0))[0]["cached"] is False
    assert len(fake.calls) == 1


def test_outage_reports_an_error_rather_than_a_price_older_than_max_age(db_file, itad):
    add_to_catalog(db_file, ("a", "Game A"))
    itad(overview("a"))
    tracker.current_prices(["a"], "US")
    age_cached_prices(db_file, 7)

    itad(error=requests.ConnectionError("down"))

    assert tracker.current_prices(["a"], "US", timedelta(hours=6))[0]["status"] == "error"
    assert tracker.current_prices(["a"], "US")[0]["status"] == "match"


def test_no_price_isnt_cached(db_file, itad):
    add_to_catalog(db_file, ("a", "Game A"))
    itad()
    assert tracker.current_prices(["a"], "US")[0]["status"] == "no_price"
    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT count(*) FROM game_library").fetchone() == (0,)


def test_current_prices_keeps_request_order_with_unknown_ids(db_file, itad):
    add_to_catalog(db_file, ("a", "Game A"))
    fake = itad(overview("a"))
    results = tracker.current_prices(["unknown", "a"], "US")
    assert results[0] == {"status": "not_in_catalog", "id": "unknown"}
    assert results[1]["status"] == "match"
    assert fake.calls[0]["ids"] == ["a"]  # ITAD isn't asked about games we can't name


def test_only_unknown_ids_make_no_call(db_file, itad):
    fake = itad()
    assert tracker.current_prices(["unknown"], "US") == [{"status": "not_in_catalog", "id": "unknown"}]
    assert fake.calls == []


def test_outage_still_returns_cached_prices(db_file, itad):
    add_to_catalog(db_file, ("a", "Game A"), ("b", "Game B"))
    itad(overview("a"))
    tracker.current_prices(["a"], "US")

    itad(error=requests.ConnectionError("down"))
    results = tracker.current_prices(["a", "b"], "US")

    assert results[0]["status"] == "match"
    assert results[1]["status"] == "error"
    assert results[1]["id"] == "b"
    assert "down" in results[1]["error"]


def test_price_response_reports_no_price_to_the_model(db_file, itad):
    itad()
    assert json.loads(tracker.price_response("a", "Game A", "US")) == {"status": "no_price", "id": "a", "title": "Game A"}
    assert '"no_price"' in tracker.SYSTEM_PROMPT


def test_itad_key_accepts_both_names(monkeypatch):
    monkeypatch.delenv("ITAD_API_KEY", raising=False)
    monkeypatch.delenv("ITAD-API-KEY", raising=False)
    with pytest.raises(tracker.MissingConfig):
        tracker.itad_api_key()
    monkeypatch.setenv("ITAD-API-KEY", "from-dotenv")
    assert tracker.itad_api_key() == "from-dotenv"
    monkeypatch.setenv("ITAD_API_KEY", "from-shell")
    assert tracker.itad_api_key() == "from-shell"
