"""When a game is likely to reach a target price, computed from data already fetched (no API calls).

The reply is split in two: a facts block in Markdown, built here so every number is right and nothing
is left out, and a short headline the LLM turns into a sentence or two in front of it. clean_intro
trims and checks that intro, and fallback_intro replaces it when it can't be trusted.

Two methods, picked by how often the game itself has been that cheap:
  - own history: it reached the target at least MIN_OWN_HITS times, so the gaps between those times
    and the seasonal sales they fell in say when it's likely again.
  - comparables: otherwise the target becomes a fraction of the launch price (a $35 target for a
    $69.99 game is 0.5), and the comparables' ages when they first got that cheap, relative to their
    own launch prices, give the projected age for this game. A fraction works across currencies and
    launch prices, and counts permanent regular-price cuts as well as sales."""
import re
import statistics
from datetime import date, datetime, timedelta

from sale_calendar import START_TOLERANCE, Sale, sale_at, upcoming_sales

MIN_OWN_HITS = 2
DAYS_PER_MONTH = 30.44
# Prices are rounded to the cent, so $34.99 counts as 50% of $69.99
PRICE_EPSILON = 0.01
# A price up to this much above the target counts as reaching it, so a game that keeps stopping at
# $35.99 counts for a $35 target and its own history is used instead of comparables
NEAR_TARGET = 0.03
ZERO_DECIMAL_CURRENCIES = {"JPY", "KRW"}
# Steam score points between the game and its comparables' median that count as a real difference
RECEPTION_GAP = 15
MAX_LISTED_COMPARABLES = 3
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


def near_target(price: float, threshold: float) -> bool:
    """Whether the price reached the threshold or came within NEAR_TARGET of it."""
    return price <= threshold * (1 + NEAR_TARGET) + PRICE_EPSILON


def reaches(history: list[dict], threshold: float) -> list[dict]:
    """The rows where the price dropped to (or near) threshold: one per stretch spent there."""
    found, below = [], False
    for row in history:
        now_below = near_target(row["price"], threshold)
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
    prices += [row["price"]
               for row in history if start <= day(row["timestamp"]) <= end]
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
        record.setdefault(sale.season, []).append(
            low is not None and near_target(low, threshold))
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


def reception_note(target: dict, comparables: list[dict]) -> str | None:
    """How the game's Steam reception compares with its comparables', when the gap is big enough to matter."""
    scores = [c["steam_score"]
              for c in comparables if c["steam_score"] is not None]
    if target.get("steam_score") is None or not scores:
        return None
    median = statistics.median(scores)
    if abs(target["steam_score"] - median) < RECEPTION_GAP:
        return None
    effect = ("an earlier or deeper cut is more likely" if target["steam_score"] < median
              else "discounts may come later or be smaller")
    return f"{target['reception']} ({target['steam_score']}%) vs {median:.0f}% for similar games, so {effect}"


def sale_window(sale: Sale) -> str:
    end = f"{sale.end.day}" if (sale.start.year, sale.start.month) == (sale.end.year, sale.end.month) \
        else f"{sale.end:%b} {sale.end.day}"
    return f"{sale.name} ({sale.start:%b} {sale.start.day} – {end})"


def short_date(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def when(projected: date, today: date) -> str:
    if projected < today:
        return f"**due at any sale now** (expected around {projected:%b %Y})"
    return f"around **{projected:%b %Y}**"


def comparable_line(c: dict, percent_off: int, used: bool) -> str:
    if c["reached"] is None:
        text = "no price data"
    elif not c["reached"]:
        text = f"never {percent_off}% off in {round(c['tracked_months'])} months"
    else:
        text = f"{percent_off}% off at {round(c['age_months'])} months ({c['date']:%b %Y}, {c['how']})"
        if not used:
            text += ", *not used: release date estimated*"
    return f"  - {c['title']} ({c['match']}): {text}"


def needs_comparables(history: list[dict], target_price: float) -> bool:
    """Whether a prediction for this history would use comparables, so they're only fetched when needed:
    the game isn't already at the target and has reached it fewer than MIN_OWN_HITS times."""
    return bool(history) and history[-1]["price"] > target_price + PRICE_EPSILON \
        and len(reaches(history, target_price)) < MIN_OWN_HITS


# How the intro may phrase the projection at each confidence
WORDING = {"low": "might", "medium": "is likely to",
           "high": "is very likely to"}
DISCLAIMER = "*Estimate from past Steam prices, not a guarantee.*"


def predict_target_price(target: dict, history: list[dict], target_price: float, comparables: list[dict],
                         comparable_histories: dict[str, list[dict]], sales: list[Sale], today: date) -> dict:
    """When the target game is likely to cost target_price or less.

    target and comparables are find_comparables' results. history is the game's history in the user's
    country, and comparable_histories maps each comparable's game_id to its US history, all oldest first.
    Returns {"status", "basis", "confidence", "projected_date", "headline", "facts", ...}: "facts" is the
    Markdown block shown to the user, "headline" the short text the LLM writes its intro from. status is
    "already_met", "predicted", "not_seen" (neither the game nor its comparables have been this cheap)
    or "no_data"."""
    title = target["title"]
    if not history:
        return {"status": "no_data", "basis": None, "confidence": None, "projected_date": None,
                "headline": f"{title}: no Steam price history yet, so no prediction",
                "facts": f"**{title}** has no Steam price history yet, so there's nothing to predict from."}

    currency = history[-1]["currency"]
    def price(amount): return format_price(amount, currency)
    current, launch = history[-1], history[0]["regular_price"]
    release = date.fromisoformat(target["release_date"]) if target.get("release_date") \
        else day(history[0]["timestamp"])
    percent_off = round((1 - target_price / launch) * 100)
    lowest = min(row["price"] for row in history)
    now = (f"{price(current['price'])} ({current['cut']}% off {price(current['regular_price'])})" if current["cut"]
           else price(current["price"]))
    now_line = f"- **Now:** {now} · all-time low {price(lowest)}"
    if target_price + PRICE_EPSILON < current["price"] and near_target(current["price"], target_price):
        now_line += f" · {price(current['price'] - target_price)} above your target"
    result = {"title": title, "target_price": target_price, "percent_off_launch": percent_off,
              "current_price": current["price"], "currency": currency}

    # Strict: a price near the target isn't "already at or below" it
    if current["price"] <= target_price + PRICE_EPSILON:
        return result | {
            "status": "already_met", "basis": None, "confidence": None, "projected_date": None,
            "headline": f"{title}: already at or below the target {price(target_price)} now",
            "facts": f"**{title}** is already {price(current['price'])} on Steam, at or below your "
            f"{price(target_price)}.\n{now_line}"}

    own = reaches(history, target_price)
    # Whether the game ever truly got to the target, or only came within NEAR_TARGET of it
    exact = any(row["price"] <= target_price + PRICE_EPSILON for row in own)
    record = seasonal_record(history, target_price, sales, today)
    compared, used = [], []
    projected, status = None, "predicted"
    if len(own) >= MIN_OWN_HITS:
        starts = [day(row["timestamp"]) for row in own]
        interval = statistics.median(
            (b - a).days for a, b in zip(starts, starts[1:]))
        projected = starts[-1] + timedelta(days=interval)
        basis, confidence = "own history", "high" if len(
            own) >= 3 else "medium"
        last = own[-1]
        projection = (f"{when(projected, today)}. It gets "
                      f"there about every {months(interval)} months; last time {short_date(day(last['timestamp']))} "
                      f"({price(last['price'])}, {reason(last, sales)})")
        why = f"from {len(own)} times in its own history"
    else:
        compared = [comparable_reach(c, comparable_histories.get(c["game_id"], []), target_price / launch,
                                     sales, today) for c in comparables]
        reached = [c for c in compared if c["reached"]]
        # An estimated release date may be a pre-order date, which makes the age too high
        used = [c for c in reached if not c["release_date_estimated"]] or reached
        if used:
            median_age = statistics.median(c["age_days"] for c in used)
            projected = release + timedelta(days=median_age)
            basis = "comparables"
            confidence = "medium" if len(used) >= 3 and all(
                c["level"] == 1 for c in used) else "low"
            projection = f"{when(projected, today)}, based on similar games"
            known = " with known release dates" if len(
                used) < len(reached) else ""
            why = f"from {len(used)} similar game{'s' if len(used) > 1 else ''}{known}"
        elif own:
            basis, confidence = "own history", "low"
            only = own[0]
            projection = (f"no date. It's been this cheap only once ({short_date(day(only['timestamp']))}, "
                          f"{price(only['price'])}, {reason(only, sales)}), too little to go on")
            why = "it's been this cheap only once"
        else:
            basis, status, confidence = None, "not_seen", "low"
            projection = (f"no date. Neither this game nor similar games have been this cheap; "
                          f"the lowest so far is {price(lowest)}")
            why = "no game has been this cheap"

    lines = [f"**{title} → {price(target_price)}** ("
             + (f"{percent_off}% off its {price(launch)} launch price)" if percent_off > 0
                else f"at or above its {price(launch)} launch price)"),
             now_line, f"- **Projected:** {projection}"]
    if own and basis == "own history" and len(own) >= MIN_OWN_HITS:
        seasons = ", ".join(
            f"{season} {sum(hits)} of {len(hits)}" for season, hits in record.items())
        nearest = min(row["price"] for row in own)
        times = (f"{len(own)} times" if exact else
                 f"{len(own)} times at {price(nearest)} (within {NEAR_TARGET:.0%} of your {price(target_price)}; never lower)")
        lines.append(f"- **Track record:** {times}" +
                     (f"; in seasonal sales: {seasons}" if seasons else ""))
    if compared:
        lines.append("- **Similar games:**")
        lines += [comparable_line(c, percent_off, c in used or not c["reached"])
                  for c in compared[:MAX_LISTED_COMPARABLES]]
        if len(compared) > MAX_LISTED_COMPARABLES:
            lines.append(
                f"  - and {len(compared) - MAX_LISTED_COMPARABLES} more")
    windows = upcoming_sales(
        max(today, projected - WINDOW_SLACK) if projected else today, sales, limit=2)
    if windows:
        # The record per season only helps when the game has reached the target before
        shown = record if basis == "own history" else {}
        lines.append("- **Sales to watch:** " + "; ".join(
            sale_window(s) + (f", {sum(shown[s.season])} of {len(shown[s.season])} past {s.season} Sales"
                              if shown.get(s.season) else "") for s in windows))
    if note := reception_note(target, comparables):
        lines.append(f"- **Reception:** {note}")
    own_cuts = regular_cuts(history, release)
    if own_cuts:
        regular = "; ".join(f"cut from {price(c['from'])} to {price(c['to'])} in {c['date']:%b %Y}, "
                            f"{round(c['age_months'])} months after release" for c in own_cuts)
    else:
        regular = "never cut"
        cut_ages = [str(round(c["regular_cuts"][0]["age_months"]))
                    for c in compared if c["regular_cuts"]]
        if cut_ages:
            regular += f"; similar games first cut theirs at {', '.join(cut_ages)} months"
    lines.append(f"- **Regular price:** {regular}")
    lines.append(f"- **Confidence:** {confidence}, {why}")
    lines.append(f"\n{DISCLAIMER}")

    if projected:
        about = "" if exact or basis != "own history" else "about "
        # A projected date that's already past would read as nonsense ("might reach $25 around January 2023")
        timing = "at any sale now" if projected < today else f"around {projected:%B %Y}"
        headline = (f"{title} {WORDING[confidence]} reach {about}{price(target_price)} {timing}, "
                    f"based on {'its own price history' if basis == 'own history' else 'similar games'}; "
                    f"confidence {confidence}")
    elif status == "not_seen":
        headline = f"{title} has never been as cheap as {price(target_price)}, nor have similar games, so it's unlikely soon"
    else:
        headline = (f"{title} has been {'as cheap as' if exact else 'near'} {price(target_price)} only once, "
                    "too little to predict a date")
    return result | {"status": status, "basis": basis, "confidence": confidence,
                     "projected_date": projected.isoformat() if projected else None, "own_reaches": len(own),
                     "comparables": compared, "headline": headline, "facts": "\n".join(lines)}


def numbers(text: str) -> set[float]:
    # As values, so "$25" in the intro matches "$25.00" in the headline
    return {float(n) for n in re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))}


# Talk about the reply itself rather than the game
META_WORDS = re.compile(
    r"\b(tool|block|result|summary|headline|data shows?)\b", re.IGNORECASE)
# Words too sure of themselves for a low-confidence prediction ("unlikely" doesn't match)
CONFIDENT_WORDS = re.compile(
    r"\b(likely|expect(ed)?|certain(ly)?|definitely|surely)\b", re.IGNORECASE)
MAX_INTRO_SENTENCES = 2


def clean_intro(reply: str, headline: str, confidence: str | None) -> str | None:
    """The LLM's intro trimmed to its first paragraph and at most two complete sentences, or None if
    it can't be trusted: numbers the headline doesn't have (the facts block holds the rest, so those
    are made up), Markdown, talk about the reply itself, or overconfident words for a low confidence.

    Only complete sentences are kept, so a reply cut off by max_tokens still gives its first sentences."""
    paragraph = (reply or "").strip().split("\n\n")[0].strip()
    sentences = [s for s in re.split(
        r"(?<=[.!?])\s+", paragraph) if re.search(r"[.!?]$", s)]
    intro = " ".join(sentences[:MAX_INTRO_SENTENCES])
    if not intro or not numbers(intro) <= numbers(headline):
        return None
    if re.search(r"[*#`|]|^\s*[-•]", intro) or META_WORDS.search(intro):
        return None
    if confidence == "low" and CONFIDENT_WORDS.search(intro):
        return None
    return intro


def fallback_intro(result: dict) -> str:
    return f"Here's my estimate for {result['title']}." if result["status"] in ("predicted", "not_seen") \
        else f"Here's what I found for {result['title']}."
