from candidates import company_key, index_company_games, match_company_games, rank_candidates, split_companies


def test_company_key_drops_case_punctuation_and_suffixes():
    assert company_key("CAPCOM Co., Ltd.") == "capcom"
    assert company_key("FromSoftware, Inc.") == "fromsoftware"
    assert company_key("Electronic Arts") == "electronic arts"
    assert company_key("Inc.") == ""


def test_split_companies_keeps_suffixes_with_their_company():
    assert split_companies("FromSoftware, Inc., Bandai Namco Entertainment") == [
        "FromSoftware, Inc.", "Bandai Namco Entertainment"]
    assert split_companies("CAPCOM Co., Ltd.") == ["CAPCOM Co., Ltd."]
    assert split_companies("SEGA, Feral Interactive") == ["SEGA", "Feral Interactive"]
    assert split_companies("") == []


COMPANY_GAMES = index_company_games([
    {"name": "elden-ring", "developers": "FromSoftware, Inc.",
     "publishers": "FromSoftware, Inc., Bandai Namco Entertainment"},
    {"name": "pragmata", "developers": "CAPCOM Co., Ltd.", "publishers": "CAPCOM Co., Ltd."},
    {"name": "street-fighter-6", "developers": "CAPCOM Co., Ltd.", "publishers": "CAPCOM Co., Ltd."},
    {"name": "nba-2k27", "publishers": "2K"},
    {"name": "no-header"},
])


def test_index_maps_developers_and_publishers_to_games():
    assert COMPANY_GAMES["capcom"] == {"pragmata", "street-fighter-6"}
    assert COMPANY_GAMES["bandai namco entertainment"] == {"elden-ring"}
    assert COMPANY_GAMES["fromsoftware"] == {"elden-ring"}


def test_query_matches_companies_as_whole_words():
    assert match_company_games("the new Capcom samurai game", COMPANY_GAMES) == {"pragmata", "street-fighter-6"}
    assert match_company_games("FromSoftware's souls game", COMPANY_GAMES) == {"elden-ring"}
    assert match_company_games("a game by Bandai Namco Entertainment", COMPANY_GAMES) == {"elden-ring"}
    assert match_company_games("nba 2k27", COMPANY_GAMES) == set()
    assert match_company_games("Ubisoft's pirate game", COMPANY_GAMES) == set()


def meta(name, **extra):
    return {"name": name, "title": name.title(), "steam_appid": 1, **extra}


def test_rank_keeps_each_games_best_chunk_and_drops_far_behind_games():
    scored = [(meta("a"), 0.30), (meta("b"), 0.48), (meta("a"), 0.40), (meta("c"), 0.10)]
    assert [c["name"] for c in rank_candidates(scored, 0.2, 0.15, 5)] == ["b", "a"]


def test_rank_returns_nothing_below_the_minimum_score():
    assert rank_candidates([(meta("a"), 0.15)], 0.2, 0.15, 5) == []
    assert rank_candidates([], 0.2, 0.15, 5) == []


def test_rank_without_minimum_keeps_low_scores():
    assert [c["name"] for c in rank_candidates([(meta("a"), 0.05)], None, 0.15, 5)] == ["a"]


def test_rank_caps_results_and_falls_back_to_name_for_title():
    scored = [({"name": f"g{i}"}, 0.5) for i in range(8)]
    candidates = rank_candidates(scored, 0.2, 0.15, 5)
    assert len(candidates) == 5
    assert candidates[0] == {"name": "g0", "title": "g0", "steam_appid": None}
