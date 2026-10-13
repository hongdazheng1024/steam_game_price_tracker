from datetime import date

import pytest

from prediction import format_price, lowest_during, predict_target_price, reaches, regular_cuts
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


def test_no_history():
    assert predict([], 30)["status"] == "no_data"


def test_already_met():
    result = predict([row("2025-03-01", 69.99), row("2026-10-01", 29.99)], 30)
    assert result["status"] == "already_met"
    assert "already at or below the target" in result["summary"]


def test_own_history_projects_from_the_gaps_between_reaches():
    history = [row("2025-03-01", 69.99),
               row("2025-06-26", 34.99), row("2025-07-10", 69.99),   # Summer Sale
               row("2025-09-15", 34.99), row("2025-09-22", 69.99),   # publisher's own sale
               row("2025-12-18", 34.99), row("2026-01-05", 69.99)]   # Winter Sale

    result = predict(history, 35)

    assert (result["status"], result["basis"], result["confidence"]) == ("predicted", "own history", "high")
    assert result["projected_date"] == "2026-03-15"  # the median gap (81 and 94 days) after the last reach
    assert "(Summer Sale 2025)" in result["summary"] and "(publisher's own sale)" in result["summary"]
    assert "Summer 1 of 2, Winter 1 of 1" in result["summary"]  # no discount in Summer 2026
    assert "reached the target in 1 of 1 past Winter Sales" in result["summary"]


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
    assert "already past, so it's overdue" in result["summary"]


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
    assert "leaving out 1 with an estimated release date" in result["summary"]
    assert "release date estimated" in result["summary"]


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
    assert "Likely sale windows: Winter Sale 2026" in result["summary"]


def test_comparables_without_history_are_reported_as_no_data():
    result = predict([row("2025-03-01", 69.99)], 35, [comparable("a")], {})
    assert result["comparables"][0]["reached"] is None
    assert "no price data" in result["summary"]


def test_weaker_reception_is_flagged():
    histories = {"a": [row("2018-01-01", 59.99, 59.99), row("2019-01-01", 29.99, 59.99)]}
    result = predict([row("2025-03-01", 69.99)], 35, [comparable("a", steam_score=89)], histories,
                     game=target(steam_score=52))
    assert "weaker than the comparables (median 89%)" in result["summary"]


def test_local_currency_uses_the_local_launch_price():
    history = [row("2025-03-01", 9900, 9900, "JPY"), row("2026-01-01", 6930, 9900, "JPY")]
    result = predict(history, 4950)
    assert result["percent_off_launch"] == 50
    assert "Target: 4,950 JPY, 50% off the launch price of 9,900 JPY." in result["summary"]
