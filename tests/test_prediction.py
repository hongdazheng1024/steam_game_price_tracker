from datetime import date

import pytest

from prediction import (clean_intro, fallback_intro, format_price, lowest_during, needs_comparables,
                        predict_target_price, reaches, regular_cuts)
from sale_calendar import Sale

TODAY = date(2026, 10, 13)
SALES = [
    Sale("Summer", date(2025, 6, 26), date(2025, 7, 10), True),
    Sale("Winter", date(2025, 12, 18), date(2026, 1, 5), True),
    Sale("Summer", date(2026, 6, 25), date(2026, 7, 9), True),
    Sale("Winter", date(2026, 12, 17), date(2027, 1, 4), True),
]


def row(day, price, regular=69.99, currency="USD"):
    return {"timestamp": f"{day}T17:00:00+00:00", "price": price, "regular_price": regular,
            "cut": round((1 - price / regular) * 100), "currency": currency}


def target(release="2025-03-01", steam_score=85):
    return {"game_id": "target", "title": "Target Game", "release_date": release, "steam_score": steam_score,
            "reception": "positive" if steam_score >= 80 else "mixed"}


def comparable(game_id, release="2018-01-01", launch=59.99, level=1, steam_score=85, estimated=False):
    return {"game_id": game_id, "title": game_id.title(), "release_date": release, "launch_price": launch,
            "level": level, "match": "same publisher and price tier", "steam_score": steam_score,
            "reception": "positive", "release_date_estimated": estimated}


def predict(history, target_price, comparables=(), histories=None, game=None):
    return predict_target_price(game or target(), history, target_price, list(comparables), histories or {},
                                SALES, TODAY)


def test_reaches_counts_each_stretch_once():
    history = [row("2025-01-01", 69.99), row("2025-02-01", 30), row("2025-02-10", 25),
               row("2025-03-01", 69.99), row("2025-04-01", 34.99)]
    assert [r["timestamp"][:10] for r in reaches(history, 35)] == ["2025-02-01", "2025-04-01"]


def test_lowest_during_counts_the_price_already_in_effect():
    history = [row("2025-06-01", 30), row("2025-06-28", 40), row("2025-08-01", 20)]
    assert lowest_during(history, date(2025, 6, 26), date(2025, 7, 10)) == 30
    assert lowest_during(history, date(2025, 1, 1), date(2025, 2, 1)) is None


def test_regular_cuts():
    history = [row("2025-03-01", 69.99), row("2026-03-01", 39.99, regular=39.99)]
    assert regular_cuts(history, date(2025, 3, 1)) == [
        {"date": date(2026, 3, 1), "from": 69.99, "to": 39.99, "age_months": 12.0}]


@pytest.mark.parametrize("amount, currency, text", [(34.99, "USD", "$34.99"), (8980, "JPY", "8,980 JPY"),
                                                    (29.99, "EUR", "29.99 EUR")])
def test_format_price(amount, currency, text):
    assert format_price(amount, currency) == text


@pytest.mark.parametrize("history, needed", [
    ([], False),                                                                    # nothing to predict from
    ([row("2025-03-01", 69.99), row("2026-01-01", 29.99)], False),                  # already at the target
    ([row("2025-03-01", 69.99)], True),                                             # never reached
    ([row("2025-03-01", 69.99), row("2025-06-26", 34.99), row("2025-07-10", 69.99)], True),   # once
    ([row("2025-03-01", 69.99), row("2025-06-26", 34.99), row("2025-07-10", 69.99),
      row("2025-12-18", 34.99), row("2026-01-05", 69.99)], False),                  # twice: own history is enough
])
def test_needs_comparables(history, needed):
    assert needs_comparables(history, 35) is needed


def test_target_above_the_launch_price():
    # Only possible after a price increase, e.g. a regional price adjustment
    result = predict([row("2025-03-01", 69.99), row("2026-01-01", 129.99, 129.99)], 100)
    assert result["facts"].startswith("**Target Game → $100.00** (at or above its $69.99 launch price)")


def test_no_history():
    assert predict([], 30)["status"] == "no_data"


def test_already_met():
    result = predict([row("2025-03-01", 69.99), row("2026-10-01", 29.99)], 30)
    assert result["status"] == "already_met"
    assert result["facts"].startswith("**Target Game** is already $29.99 on Steam, at or below your $30.00.")


def test_own_history_projects_from_the_gaps_between_reaches():
    history = [row("2025-03-01", 69.99),
               row("2025-06-26", 34.99), row("2025-07-10", 69.99),   # Summer Sale
               row("2025-09-15", 34.99), row("2025-09-22", 69.99),   # publisher's own sale
               row("2025-12-18", 34.99), row("2026-01-05", 69.99)]   # Winter Sale

    result = predict(history, 35)

    assert (result["status"], result["basis"], result["confidence"]) == ("predicted", "own history", "high")
    assert result["projected_date"] == "2026-03-15"  # the median gap (81 and 94 days) after the last reach
    assert "last time Dec 18, 2025 ($34.99, Winter Sale 2025)" in result["facts"]
    assert "- **Track record:** 3 times; in seasonal sales: Summer 1 of 2, Winter 1 of 1" in result["facts"]  # no discount in Summer 2026
    assert "Winter Sale 2026 (Dec 17 – Jan 4), 1 of 1 past Winter Sales" in result["facts"]
    assert "- **Confidence:** high, from 3 times in its own history" in result["facts"]
    assert result["headline"] == ("Target Game is very likely to reach $35.00 at any sale now, based on its own "
                                  "price history; confidence high")  # projected March 2026, before TODAY


def test_comparables_are_matched_by_fraction_of_launch_price():
    # $35 is 50% of $69.99. For $59.99 comparables that's $30, so $34.99 isn't cheap enough.
    history = [row("2025-03-01", 69.99), row("2026-01-01", 49.99)]
    comparables = [comparable("early"), comparable("late"), comparable("never")]
    histories = {
        "early": [row("2018-01-01", 59.99, 59.99), row("2019-01-01", 29.99, 59.99)],    # 12 months
        "late": [row("2018-01-01", 59.99, 59.99), row("2018-07-01", 34.99, 59.99),
                 row("2020-01-01", 29.99, 59.99)],                                        # 24 months
        "never": [row("2018-01-01", 59.99, 59.99), row("2019-01-01", 34.99, 59.99)],
    }

    result = predict(history, 35, comparables, histories)

    assert (result["basis"], result["confidence"]) == ("comparables", "low")
    assert [c["reached"] for c in result["comparables"]] == [True, True, False]
    assert [c.get("age_months") for c in result["comparables"]] == [12.0, 24.0, None]
    assert result["projected_date"] == "2026-08-30"  # the median age (365 and 730 days) after release
    assert "- **Projected:** **due at any sale now** (expected around Aug 2026), based on similar games" in result["facts"]
    assert result["headline"] == ("Target Game might reach $35.00 at any sale now, based on similar games; "
                                  "confidence low")


def test_a_regular_price_cut_counts_as_reaching_the_target():
    histories = {"cut": [row("2018-01-01", 59.99, 59.99), row("2019-01-01", 29.99, 29.99)]}

    [result] = predict([row("2025-03-01", 69.99)], 35, [comparable("cut")], histories)["comparables"]

    assert (result["reached"], result["how"]) == (True, "regular price cut")


def test_three_level_one_comparables_give_medium_confidence():
    histories = {name: [row("2018-01-01", 59.99, 59.99), row("2019-01-01", 29.99, 59.99)] for name in "abc"}
    result = predict([row("2025-03-01", 69.99)], 35, [comparable(n) for n in "abc"], histories)
    assert result["confidence"] == "medium"


def test_estimated_release_dates_are_left_out_when_known_ones_exist():
    histories = {"exact": [row("2018-01-01", 59.99, 59.99), row("2019-01-01", 29.99, 59.99)],
                 "estimated": [row("2016-01-01", 59.99, 59.99), row("2019-01-01", 29.99, 59.99)]}
    comparables = [comparable("exact"), comparable("estimated", release="2016-01-01", estimated=True)]

    result = predict([row("2025-03-01", 69.99)], 35, comparables, histories)

    assert result["projected_date"] == "2026-03-01"  # from "exact" alone: 12 months
    assert "- **Confidence:** low, from 1 similar game with known release dates" in result["facts"]
    assert "*not used: release date estimated*" in result["facts"]


def test_estimated_release_dates_are_used_when_theres_nothing_else():
    histories = {"estimated": [row("2016-01-01", 59.99, 59.99), row("2017-01-01", 29.99, 59.99)]}
    comparables = [comparable("estimated", release="2016-01-01", estimated=True)]

    assert predict([row("2025-03-01", 69.99)], 35, comparables, histories)["projected_date"] == "2026-03-02"  # 366 days: 2016 is a leap year


def test_target_never_seen():
    histories = {"a": [row("2018-01-01", 59.99, 59.99), row("2019-01-01", 29.99, 59.99)]}
    result = predict([row("2025-03-01", 69.99), row("2026-01-01", 29.99), row("2026-01-10", 69.99)], 10,
                     [comparable("a")], histories)
    assert (result["status"], result["projected_date"]) == ("not_seen", None)


def test_one_own_reach_without_comparables_is_too_little():
    history = [row("2025-03-01", 69.99), row("2025-06-26", 34.99), row("2025-07-10", 69.99)]
    result = predict(history, 35)
    assert (result["status"], result["basis"], result["confidence"], result["projected_date"]) == \
        ("predicted", "own history", "low", None)


def test_sale_windows_include_one_shortly_before_the_projected_date():
    # Projected 2027-01-25 (697 days after release); the Winter Sale ending 2027-01-04 still counts
    histories = {"a": [row("2018-01-01", 59.99, 59.99), row("2019-11-29", 29.99, 59.99)]}
    result = predict([row("2025-02-27", 69.99)], 35, [comparable("a")], histories, game=target("2025-02-27"))
    assert result["projected_date"] == "2027-01-25"
    assert "- **Sales to watch:** Winter Sale 2026" in result["facts"]
    assert "- **Projected:** around **Jan 2027**, based on similar games" in result["facts"]
    assert result["headline"] == "Target Game might reach $35.00 around January 2027, based on similar games; confidence low"


def test_comparables_without_history_are_reported_as_no_data():
    result = predict([row("2025-03-01", 69.99)], 35, [comparable("a")], {})
    assert result["comparables"][0]["reached"] is None
    assert "no price data" in result["facts"]


def test_weaker_reception_is_flagged():
    histories = {"a": [row("2018-01-01", 59.99, 59.99), row("2019-01-01", 29.99, 59.99)]}
    result = predict([row("2025-03-01", 69.99)], 35, [comparable("a", steam_score=89)], histories,
                     game=target(steam_score=52))
    assert "- **Reception:** mixed (52%) vs 89% for similar games, so an earlier or deeper cut is more likely" in result["facts"]


def test_local_currency_uses_the_local_launch_price():
    history = [row("2025-03-01", 9900, 9900, "JPY"), row("2026-01-01", 6930, 9900, "JPY")]
    result = predict(history, 4950)
    assert result["percent_off_launch"] == 50
    assert result["facts"].startswith("**Target Game → 4,950 JPY** (50% off its 9,900 JPY launch price)")


def test_only_the_top_comparables_are_listed():
    histories = {n: [row("2018-01-01", 59.99, 59.99), row("2019-01-01", 29.99, 59.99)] for n in "abcde"}
    result = predict([row("2025-03-01", 69.99)], 35, [comparable(n) for n in "abcde"], histories)
    assert [line.split(" (")[0] for line in result["facts"].splitlines() if line.startswith("  - ")] == \
        ["  - A", "  - B", "  - C", "  - and 2 more"]


def test_regular_price_line_falls_back_to_comparables_cuts():
    histories = {"a": [row("2018-01-01", 59.99, 59.99), row("2019-07-01", 29.99, 29.99)]}
    result = predict([row("2025-03-01", 69.99)], 20, [comparable("a")], histories)
    assert "- **Regular price:** never cut; similar games first cut theirs at 18 months" in result["facts"]


def test_low_confidence_headline_says_might():
    histories = {"a": [row("2018-01-01", 59.99, 59.99), row("2019-01-01", 29.99, 59.99)]}
    result = predict([row("2025-03-01", 69.99)], 35, [comparable("a")], histories)
    assert result["headline"] == "Target Game might reach $35.00 at any sale now, based on similar games; confidence low"
    assert result["facts"].endswith("*Estimate from past Steam prices, not a guarantee.*")


HEADLINE = "Monster Hunter Wilds might reach $25.00 around January 2027, based on similar games; confidence low"


@pytest.mark.parametrize("reply, intro", [
    ("Monster Hunter Wilds might hit $25 around January 2027, but it's hard to call.",
     "Monster Hunter Wilds might hit $25 around January 2027, but it's hard to call."),
    ("It might get there by early 2027. Similar games took a while. Patience pays off.",    # two sentences kept
     "It might get there by early 2027. Similar games took a while."),
    ("It might reach $25 around January 2027.\n\n**More details:** it was cheap before.",  # first paragraph only
     "It might reach $25 around January 2027."),
    ("It might reach $25 around January 2027. Keep an eye on the Steam sal",               # cut off by max_tokens
     "It might reach $25 around January 2027."),
    ("It might drop to $25 around Black Friday, November 27.", None),       # a date of its own
    ("It usually drops to $23-$25 within a year.", None),                    # numbers of its own
    ("The result contains a block with more detailed information.", None),   # talks about the reply
    ("- It might reach $25 in January 2027.", None),                         # Markdown
    ("It's likely that it will reach $25 in January 2027.", None),           # too sure for low confidence
    ("It's unlikely to be soon, but it might reach $25 in January 2027.",
     "It's unlikely to be soon, but it might reach $25 in January 2027."),
    ("Keep an eye on the Steam sal", None),                                  # no complete sentence
    ("", None),
])
def test_clean_intro(reply, intro):
    assert clean_intro(reply, HEADLINE, "low") == intro


def test_confident_words_are_fine_for_high_confidence():
    headline = "Hades is very likely to reach $10.00 around November 2026, based on its own price history; confidence high"
    intro = "Hades is very likely to hit $10 around November 2026."
    assert clean_intro(intro, headline, "high") == intro


def test_fallback_intro():
    assert fallback_intro({"title": "Hades", "status": "predicted"}) == "Here's my estimate for Hades."
    assert fallback_intro({"title": "Hades", "status": "already_met"}) == "Here's what I found for Hades."


# Elden Ring's Steam history from IsThereAnyDeal: its deepest discount is 40% ($35.99), 2.8% above $35
ELDEN_RING = [row(d, p, 59.99) for d, p in [
    ("2022-02-25", 59.99), ("2022-12-22", 41.99), ("2023-01-05", 59.99), ("2023-06-29", 41.99),
    ("2023-07-13", 59.99), ("2023-10-05", 39.59), ("2023-10-19", 59.99), ("2023-12-21", 35.99),
    ("2024-01-04", 59.99), ("2024-06-27", 41.99), ("2024-07-11", 59.99), ("2024-09-19", 41.99),
    ("2024-09-26", 59.99), ("2024-12-19", 35.99), ("2025-01-02", 59.99), ("2025-03-13", 35.99),
    ("2025-03-20", 59.99), ("2025-06-26", 35.99), ("2025-07-10", 59.99), ("2025-12-18", 38.99),
    ("2026-01-05", 59.99), ("2026-03-19", 38.99), ("2026-03-26", 59.99)]]


@pytest.mark.parametrize("price, counts", [(35.00, True), (35.99, True), (36.05, True), (36.10, False)])
def test_a_price_within_3_percent_counts_as_reaching_the_target(price, counts):
    assert bool(reaches([row("2025-01-01", 59.99, 59.99), row("2025-06-01", price, 59.99)], 35)) is counts


def test_near_misses_use_the_games_own_history():
    assert not needs_comparables(ELDEN_RING, 35)

    result = predict(ELDEN_RING, 35, game=target("2022-02-25"))

    assert (result["basis"], result["confidence"]) == ("own history", "high")
    assert "- **Track record:** 4 times at $35.99 (within 3% of your $35.00; never lower)" in result["facts"]
    assert "last time Jun 26, 2025 ($35.99, Summer Sale 2025)" in result["facts"]
    assert result["headline"] == ("Target Game is very likely to reach about $35.00 at any sale now, based on its own "
                                  "price history; confidence high")


def test_exact_reaches_dont_say_about():
    result = predict(ELDEN_RING, 36, game=target("2022-02-25"))
    assert "- **Track record:** 4 times; in seasonal sales" in result["facts"]
    assert "reach $36.00 at any sale now" in result["headline"]


def test_a_current_price_near_the_target_isnt_already_met():
    result = predict(ELDEN_RING[:-1], 35, game=target("2022-02-25"))   # on sale at $38.99: 11% above
    assert result["status"] == "predicted"
    near = predict(ELDEN_RING + [row("2026-06-25", 35.99, 59.99)], 35, game=target("2022-02-25"))
    assert near["status"] == "predicted"
    assert "· $0.99 above your target" in near["facts"]
