import json
from datetime import date

import pytest

from sale_calendar import Sale, load_sales, sale_at, tag_discounts, upcoming_sales

SUMMER = Sale("Summer", date(2025, 6, 26), date(2025, 7, 10), True)
AUTUMN = Sale("Autumn", date(2025, 9, 29), date(2025, 10, 6), True)
WINTER = Sale("Winter", date(2025, 12, 18), date(2026, 1, 5), True)
SALES = [SUMMER, AUTUMN, WINTER]


def write_sales(tmp_path, sales):
    path = tmp_path / "sales.json"
    path.write_text(json.dumps({"sales": sales}), encoding="utf-8")
    return path


def test_the_shipped_calendar_loads():
    sales = load_sales()
    assert sales[0].name == "Summer Sale 2016"
    assert [s.start for s in sales] == sorted(s.start for s in sales)


def test_sale_name_uses_the_start_year():
    assert WINTER.name == "Winter Sale 2025"


@pytest.mark.parametrize("timestamp, expected", [
    ("2025-06-26T17:05:00+00:00", "Summer Sale 2025"),   # first day
    # a day early, within tolerance
    ("2025-06-25T23:00:00+00:00", "Summer Sale 2025"),
    ("2025-06-24T23:00:00+00:00", None),                 # too early
    ("2025-07-10T16:00:00+00:00", "Summer Sale 2025"),   # last day
    ("2025-07-11T00:00:00+00:00", None),                 # after it ended
    ("2026-01-03T12:00:00+00:00", "Winter Sale 2025"),   # crosses the new year
])
def test_sale_at(timestamp, expected):
    sale = sale_at(timestamp, SALES)
    assert (sale.name if sale else None) == expected


def test_only_discounts_are_tagged():
    history = [
        {"timestamp": "2025-06-26T17:00:00+00:00",
            "price": 29.99, "cut": 50},  # summer sale
        {"timestamp": "2025-07-10T17:00:00+00:00", "price": 59.99,
            "cut": 0},   # back to regular, still in window
        {"timestamp": "2025-08-14T17:00:00+00:00",
            "price": 41.99, "cut": 30},  # publisher's own sale
    ]

    assert [row["sale"] for row in tag_discounts(history, SALES)] == [
        "Summer Sale 2025", None, None]
    assert tag_discounts(history, SALES)[
        0]["price"] == 29.99  # other fields kept


def test_upcoming_sales_include_one_on_now():
    assert upcoming_sales(date(2025, 10, 1), SALES) == [AUTUMN, WINTER]
    assert upcoming_sales(date(2025, 10, 7), SALES, limit=1) == [WINTER]
    assert upcoming_sales(date(2026, 2, 1), SALES) == []


def test_sales_are_sorted(tmp_path):
    path = write_sales(tmp_path, [{"season": "Winter", "start": "2025-12-18", "end": "2026-01-05"},
                                  {"season": "Summer", "start": "2025-06-26", "end": "2025-07-10"}])
    assert [s.season for s in load_sales(path)] == ["Summer", "Winter"]


@pytest.mark.parametrize("entry", [
    {"season": "Summer", "start": "2025-06-26"},                         # no end
    {"season": "Summer", "start": "June 26", "end": "2025-07-10"},       # bad date
    {"season": "Summer", "start": "2025-07-10",
        "end": "2025-06-26"},    # ends before it starts
])
def test_malformed_entries_are_rejected(tmp_path, entry):
    with pytest.raises(ValueError):
        load_sales(write_sales(tmp_path, [entry]))


def test_overlapping_sales_are_rejected(tmp_path):
    path = write_sales(tmp_path, [{"season": "Summer", "start": "2025-06-26", "end": "2025-07-10"},
                                  {"season": "Halloween", "start": "2025-07-05", "end": "2025-07-12"}])
    with pytest.raises(ValueError, match="overlap"):
        load_sales(path)
