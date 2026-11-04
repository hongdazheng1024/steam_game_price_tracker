import json
import sqlite3
from types import SimpleNamespace

import pytest

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


def test_current_price_needs_the_game_in_the_catalog(db_file):
    result = tracker.current_price("unknown", "US")
    assert result["status"] == "error"
    assert result["id"] == "unknown"


def test_current_price_uses_the_cached_price(db_file):
    with sqlite3.connect(db_file) as conn:
        conn.execute("INSERT INTO game_catalog (id, official_title, normalized_title, created_at) VALUES (?, ?, ?, ?)",
                     ("hades", "Hades", "hades", "2026-10-01T00:00:00"))
        conn.execute("INSERT INTO game_library VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))",
                     ("hades", "Hades", "US", json.dumps({"price": {"amount": 12.49}}),
                      json.dumps({"price": {"amount": 9.99}})))
    result = tracker.current_price("hades", "US")
    assert result == {"status": "match", "id": "hades", "title": "Hades", "current_deal": {"price": {"amount": 12.49}},
                      "historical_lowest": {"price": {"amount": 9.99}}, "cached": True}


def test_itad_key_accepts_both_names(monkeypatch):
    monkeypatch.delenv("ITAD_API_KEY", raising=False)
    monkeypatch.delenv("ITAD-API-KEY", raising=False)
    with pytest.raises(tracker.MissingConfig):
        tracker.itad_api_key()
    monkeypatch.setenv("ITAD-API-KEY", "from-dotenv")
    assert tracker.itad_api_key() == "from-dotenv"
    monkeypatch.setenv("ITAD_API_KEY", "from-shell")
    assert tracker.itad_api_key() == "from-shell"
