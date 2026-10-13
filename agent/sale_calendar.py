"""Steam's seasonal sale windows, from data/steam_sales.json, for telling which discounts were seasonal sales.

price_history only records that a price changed, not why. A discount that starts inside a seasonal
sale's window is counted as part of that sale; any other discount is the publisher's own."""
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

SALES_PATH = Path(__file__).parent / "data" / "steam_sales.json"
# Sales start at 10am Pacific, which is still the day before in some time zones, so a discount
# starting up to a day before the window still counts
START_TOLERANCE = timedelta(days=1)


@dataclass(frozen=True)
class Sale:
    season: str
    start: date
    end: date
    verified: bool
    note: str | None = None

    @property
    def name(self) -> str:
        return f"{self.season} Sale {self.start.year}"


def load_sales(path: Path = SALES_PATH) -> list[Sale]:
    """The sales in the file, oldest first. Raises ValueError for a malformed entry."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    sales = []
    for i, entry in enumerate(raw["sales"]):
        try:
            sale = Sale(season=entry["season"], start=date.fromisoformat(entry["start"]),
                        end=date.fromisoformat(entry["end"]), verified=bool(entry.get("verified", False)),
                        note=entry.get("note"))
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(
                f"sale #{i + 1} in {path.name} is malformed: {entry!r} ({e})") from e
        if sale.end < sale.start:
            raise ValueError(
                f"{sale.name} in {path.name} ends before it starts")
        sales.append(sale)

    sales.sort(key=lambda sale: sale.start)
    for earlier, later in zip(sales, sales[1:]):
        if later.start <= earlier.end:
            raise ValueError(
                f"{earlier.name} and {later.name} in {path.name} overlap")
    return sales


def sale_at(timestamp: str, sales: list[Sale]) -> Sale | None:
    """The seasonal sale a price change at this timestamp (ISO 8601, as in price_history) belongs to."""
    day = datetime.fromisoformat(timestamp).date()
    return next((sale for sale in sales if sale.start - START_TOLERANCE <= day <= sale.end), None)


def tag_discounts(history: list[dict], sales: list[Sale]) -> list[dict]:
    """History rows, each with "sale": the seasonal sale's name for a discount that started in one,
    else None (a publisher's own discount, or a row that isn't a discount)."""
    tagged = []
    for row in history:
        sale = sale_at(row["timestamp"], sales) if row["cut"] > 0 else None
        tagged.append(row | {"sale": sale.name if sale else None})
    return tagged


def upcoming_sales(today: date, sales: list[Sale], limit: int = 3) -> list[Sale]:
    """The next sales that haven't ended by today, including one that's on now."""
    return [sale for sale in sales if sale.end >= today][:limit]
