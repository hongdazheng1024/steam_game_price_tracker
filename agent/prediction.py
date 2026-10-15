"""When a game is likely to reach a target price, computed from data already fetched (no API calls),
so the LLM only has to explain the result.

Two methods, picked by how often the game itself has been that cheap:
  - own history: it reached the target at least MIN_OWN_HITS times, so the gaps between those times
    and the seasonal sales they fell in say when it's likely again.
  - comparables: otherwise the target becomes a fraction of the launch price (a $35 target for a
    $69.99 game is 0.5), and the comparables' ages when they first got that cheap, relative to their
    own launch prices, give the projected age for this game. A fraction works across currencies and
    launch prices, and counts permanent regular-price cuts as well as sales."""
import statistics
from datetime import date, datetime, timedelta

from sale_calendar import START_TOLERANCE, Sale, sale_at, upcoming_sales

MIN_OWN_HITS = 2
DAYS_PER_MONTH = 30.44
# Prices are rounded to the cent, so $34.99 counts as 50% of $69.99
PRICE_EPSILON = 0.01
ZERO_DECIMAL_CURRENCIES = {"JPY", "KRW"}
# Steam score points between the game and its comparables' median that count as a real difference
RECEPTION_GAP = 15
MAX_LISTED_REACHES = 5
# A projected date is rough, so a sale ending up to this long before it is still a likely window
WINDOW_SLACK = timedelta(weeks=6)


def day(timestamp: str) -> date:
    return datetime.fromisoformat(timestamp).date()


def months(days: float) -> float:
    return round(days / DAYS_PER_MONTH, 1)


def format_price(amount: float, currency: str) -> str:
    if currency == "USD":
        return f"${amount:,.2f}"
    if currency in ZERO_DECIMAL_CURRENCIES:
        return f"{amount:,.0f} {currency}"
    return f"{amount:,.2f} {currency}"


def reaches(history: list[dict], threshold: float) -> list[dict]:
    """The rows where the price dropped to threshold or below: one per stretch spent there."""
    found, below = [], False
    for row in history:
        now_below = row["price"] <= threshold + PRICE_EPSILON
        if now_below and not below:
            found.append(row)
        below = now_below
    return found


def regular_cuts(history: list[dict], release: date) -> list[dict]:
    """Permanent price cuts: the rows where the regular price went down."""
    return [{"date": day(row["timestamp"]), "from": prev["regular_price"], "to": row["regular_price"],
             "age_months": months(max(0, (day(row["timestamp"]) - release).days))}
            for prev, row in zip(history, history[1:])
            if row["regular_price"] < prev["regular_price"] - PRICE_EPSILON]


def lowest_during(history: list[dict], start: date, end: date) -> float | None:
    """The lowest price in effect from start to end, or None if the history doesn't reach back that far."""
    before = [row for row in history if day(row["timestamp"]) < start]
    prices = [before[-1]["price"]] if before else []
    prices += [row["price"] for row in history if start <= day(row["timestamp"]) <= end]
    return min(prices) if prices else None


def seasonal_record(history: list[dict], threshold: float, sales: list[Sale], today: date) -> dict:
    """{season: [True if that sale brought the price to threshold, ...]} for finished sales since the
    game was first priced."""
    if not history:
        return {}
    first = day(history[0]["timestamp"])
    record = {}
    for sale in sales:
        if sale.start < first or sale.end >= today:
            continue
        low = lowest_during(history, sale.start - START_TOLERANCE, sale.end)
        record.setdefault(sale.season, []).append(low is not None and low <= threshold + PRICE_EPSILON)
    return record


def reason(row: dict, sales: list[Sale]) -> str:
    if row["cut"] == 0:
        return "regular price cut"
    sale = sale_at(row["timestamp"], sales)
    return sale.name if sale else "publisher's own sale"


def comparable_reach(comparable: dict, history: list[dict], fraction: float, sales: list[Sale],
                     today: date) -> dict:
    """When the comparable first cost fraction of its launch price, and its regular-price cuts."""
    result = {"title": comparable["title"], "match": comparable["match"], "reception": comparable["reception"],
              "steam_score": comparable["steam_score"], "level": comparable["level"],
              "launch_price": comparable["launch_price"],
              "release_date_estimated": comparable.get("release_date_estimated", False),
              "reached": None, "regular_cuts": []}
    if not history or comparable["launch_price"] is None:
        return result  # no data
    release = date.fromisoformat(comparable["release_date"])
    result["regular_cuts"] = regular_cuts(history, release)
    found = reaches(history, fraction * comparable["launch_price"])
    if not found:
        return result | {"reached": False, "tracked_months": months((today - release).days)}
    first = found[0]
    age_days = max(0, (day(first["timestamp"]) - release).days)
    return result | {"reached": True, "age_days": age_days, "age_months": months(age_days),
                     "date": day(first["timestamp"]), "how": reason(first, sales)}


def describe_comparable(c: dict, percent_off: int) -> str:
    estimated = ", release date estimated" if c["release_date_estimated"] else ""
    head = f"- {c['title']} ({c['match']}, reception {c['reception'] or 'unknown'}{estimated})"
    if c["reached"] is None:
        return f"{head}: no price data"
    if not c["reached"]:
        return f"{head}: never reached {percent_off}% off in {c['tracked_months']} months"
    return f"{head}: reached {percent_off}% off at {c['age_months']} months ({c['date']}, {c['how']})"


def reception_signal(target: dict, comparables: list[dict]) -> str | None:
    scores = [c["steam_score"] for c in comparables if c["steam_score"] is not None]
    if target.get("steam_score") is None or not scores:
        return None
    median = statistics.median(scores)
    if target["steam_score"] <= median - RECEPTION_GAP:
        return (f"Reception: {target['reception']} ({target['steam_score']}% positive), weaker than the "
                f"comparables (median {median:.0f}%), so an earlier or deeper cut is more likely.")
    if target["steam_score"] >= median + RECEPTION_GAP:
        return (f"Reception: {target['reception']} ({target['steam_score']}% positive), stronger than the "
                f"comparables (median {median:.0f}%), so discounts may come later or be smaller.")
    return None


def describe_sale(sale: Sale, record: dict) -> str:
    text = f"{sale.name} ({sale.start:%b %d} - {sale.end:%b %d})"
    past = record.get(sale.season)
    if past:
        text += f", reached the target in {sum(past)} of {len(past)} past {sale.season} Sales"
    return text


def needs_comparables(history: list[dict], target_price: float) -> bool:
    """Whether a prediction for this history would use comparables, so they're only fetched when needed:
    the game isn't already at the target and has reached it fewer than MIN_OWN_HITS times."""
    return bool(history) and history[-1]["price"] > target_price + PRICE_EPSILON \
        and len(reaches(history, target_price)) < MIN_OWN_HITS


def predict_target_price(target: dict, history: list[dict], target_price: float, comparables: list[dict],
                         comparable_histories: dict[str, list[dict]], sales: list[Sale], today: date) -> dict:
    """When the target game is likely to cost target_price or less.

    target and comparables are find_comparables' results. history is the game's history in the user's
    country, and comparable_histories maps each comparable's game_id to its US history, all oldest first.
    Returns {"status", "basis", "confidence", "projected_date", "summary", ...}; "summary" is the text
    for the LLM. status is "already_met", "predicted", "not_seen" (neither the game nor its
    comparables have been this cheap) or "no_data"."""
    title = target["title"]
    if not history:
        return {"status": "no_data", "basis": None, "confidence": None, "projected_date": None,
                "summary": f"{title} has no Steam price history yet, so there's nothing to predict from."}

    currency = history[-1]["currency"]
    price = lambda amount: format_price(amount, currency)
    current, launch = history[-1], history[0]["regular_price"]
    release = date.fromisoformat(target["release_date"]) if target.get("release_date") \
        else day(history[0]["timestamp"])
    fraction = target_price / launch
    percent_off = round((1 - fraction) * 100)

    discount = f", {current['cut']}% off" if current["cut"] else ""
    lines = [
        f"Game: {title}, released {release} ({months((today - release).days)} months ago).",
        f"Target: {price(target_price)}, {percent_off}% off the launch price of {price(launch)}." if percent_off > 0
        else f"Target: {price(target_price)}, at or above the launch price of {price(launch)}.",
        f"Current price: {price(current['price'])} (regular {price(current['regular_price'])}{discount}). "
        f"All-time low: {price(min(row['price'] for row in history))}.",
    ]
    result = {"target_price": target_price, "percent_off_launch": percent_off, "current_price": current["price"],
              "currency": currency}
    if current["price"] <= target_price + PRICE_EPSILON:
        lines.append("The current price is already at or below the target.")
        return result | {"status": "already_met", "basis": None, "confidence": None, "projected_date": None,
                         "summary": "\n".join(lines)}

    # The game's own history
    own = reaches(history, target_price)
    record = seasonal_record(history, target_price, sales, today)
    if own:
        listed = "; ".join(f"{day(row['timestamp'])} at {price(row['price'])} ({reason(row, sales)})"
                           for row in own[-MAX_LISTED_REACHES:])
        lines.append(f"Own history: reached the target {len(own)} time(s). Most recent: {listed}.")
    else:
        lines.append(f"Own history: never at or below the target ({len(history)} price changes on record).")
    if own and record:
        lines.append("Past seasonal sales that reached the target: "
                     + ", ".join(f"{season} {sum(hits)} of {len(hits)}" for season, hits in record.items()) + ".")
    own_cuts = regular_cuts(history, release)
    lines.append("Regular price cuts: " + ("; ".join(f"{c['date']} {price(c['from'])} -> {price(c['to'])} "
                                                     f"(at {c['age_months']} months)" for c in own_cuts)
                                           if own_cuts else "none so far") + ".")

    # Comparables, only needed when the game's own history is thin
    compared = []
    projected, basis, status = None, None, "predicted"
    if len(own) >= MIN_OWN_HITS:
        starts = [day(row["timestamp"]) for row in own]
        interval = statistics.median((b - a).days for a, b in zip(starts, starts[1:]))
        projected = starts[-1] + timedelta(days=interval)
        basis, confidence = "own history", "high" if len(own) >= 3 else "medium"
        lines.append(f"Projection: it reaches the target about every {months(interval)} months; last time "
                     f"{starts[-1]}, so next around {projected:%b %Y}"
                     + (" (already due)." if projected < today else "."))
    else:
        compared = [comparable_reach(c, comparable_histories.get(c["game_id"], []), fraction, sales, today)
                    for c in comparables]
        reached = [c for c in compared if c["reached"]]
        if compared:
            lines.append(f"Comparables (their first time at {percent_off}% off their launch price or less):")
            lines += [describe_comparable(c, percent_off) for c in compared]
            cut_ages = [f"{c['title']} at {c['regular_cuts'][0]['age_months']} months"
                        for c in compared if c["regular_cuts"]]
            if cut_ages:
                lines.append("Comparables' first regular-price cuts: " + "; ".join(cut_ages) + ".")
        if reached:
            # An estimated release date may be a pre-order date, which makes the age too high
            used = [c for c in reached if not c["release_date_estimated"]] or reached
            median_age = statistics.median(c["age_days"] for c in used)
            projected = release + timedelta(days=median_age)
            basis = "comparables"
            confidence = "medium" if len(used) >= 3 and all(c["level"] == 1 for c in used) else "low"
            left_out = len(reached) - len(used)
            lines.append(f"Projection: {len(reached)} of {len(compared)} comparables got there, after a median of "
                         f"{months(median_age)} months"
                         + (f" (leaving out {left_out} with an estimated release date)" if left_out else "")
                         + f", which for {title} is around {projected:%b %Y}"
                         + (" (already past, so it's overdue)." if projected < today else "."))
        elif own:
            basis, confidence = "own history", "low"
            lines.append("Projection: it reached the target only once, and no comparable has been this cheap, "
                         "so there's too little to project a date.")
        else:
            status, confidence = "not_seen", "low"
            lines.append("Projection: neither this game nor its comparables have ever been this cheap, "
                         "so the target is unlikely soon.")

    if signal := reception_signal(target, comparables):
        lines.append(signal)
    windows = upcoming_sales(max(today, projected - WINDOW_SLACK) if projected else today, sales, limit=2)
    if windows:
        # The record per season only helps when the game has reached the target before
        shown = record if basis == "own history" else {}
        lines.append("Likely sale windows: " + "; ".join(describe_sale(s, shown) for s in windows) + ".")
    lines.append(f"Confidence: {confidence}.")

    return result | {"status": status, "basis": basis, "confidence": confidence,
                     "projected_date": projected.isoformat() if projected else None,
                     "own_reaches": len(own), "comparables": compared, "summary": "\n".join(lines)}
