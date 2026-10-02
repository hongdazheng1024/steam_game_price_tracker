import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date

from bs4 import BeautifulSoup
from markdownify import markdownify

from .common import http_client

STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
STORE_SEARCH_RESULTS_URL = "https://store.steampowered.com/search/results/"
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
GAMES_ONLY_CATEGORY = 998  # excludes DLC, software, soundtracks, etc.


@dataclass
class GameOverview:
    appid: int
    name: str
    developers: list[str]
    publishers: list[str]
    genres: list[str]
    release_date: str
    is_free: bool
    price: str | None
    metacritic_score: int | None
    platforms: list[str]
    short_description: str
    about_the_game_md: str
    store_url: str = field(init=False)

    def __post_init__(self):
        self.store_url = f"https://store.steampowered.com/app/{self.appid}/"


def find_appid(game_name: str) -> tuple[int, str] | None:
    """Resolve a game name to a Steam appid via the store search API. Returns (appid, matched_name)."""
    with http_client() as client:
        resp = client.get(STORE_SEARCH_URL, params={
                          "term": game_name, "l": "english", "cc": "US"})
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return None
        top = items[0]
        return top["id"], top["name"]


def html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for media in soup.find_all(["video", "source", "img"]):
        media.decompose()
    text = markdownify(str(soup), heading_style="ATX")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def iter_appids_by_tag(tag_id: int, page_size: int = 50, page_delay: float = 1.0) -> Iterator[int]:
    """Lazily yield game appids for a Steam tag, paging through the store search results."""
    start = 0
    with http_client() as client:
        while True:
            resp = client.get(
                STORE_SEARCH_RESULTS_URL,
                params={
                    "query": "",
                    "start": start,
                    "count": page_size,
                    "tags": tag_id,
                    "category1": GAMES_ONLY_CATEGORY,
                    "json": 1,
                    "infinite": 1,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            appids = [int(a) for a in re.findall(
                r'data-ds-appid="(\d+)"', payload.get("results_html", ""))]
            if not appids:
                return
            yield from appids
            start += page_size
            if start >= payload.get("total_count", 0):
                return
            time.sleep(page_delay)


def get_game_overview(appid: int) -> GameOverview | None:
    with http_client() as client:
        resp = client.get(APP_DETAILS_URL, params={
                          "appids": appid, "cc": "us", "l": "english"})
        resp.raise_for_status()
        payload = resp.json().get(str(appid))
        if not payload or not payload.get("success"):
            return None
        data = payload["data"]

    price_overview = data.get("price_overview")
    if data.get("is_free"):
        price = "Free"
    elif price_overview:
        # price = price_overview.get("initial_formatted") or None
        if (price_overview.get("initial_formatted")):
            price = price_overview.get("initial_formatted")
        else:
            price = f"${str(price_overview.get('initial') / 100)}" or None
    else:
        price = None

    metacritic = data.get("metacritic") or {}
    platforms = data.get("platforms") or []
    availablePlatforms = []
    for p in platforms:
        if (platforms.get(p) == True):
            availablePlatforms.append(p)

    return GameOverview(
        appid=data["steam_appid"],
        name=data["name"],
        developers=data.get("developers", []),
        publishers=data.get("publishers", []),
        genres=[g["description"] for g in data.get("genres", [])],
        release_date=(data.get("release_date") or {}).get("date", "Unknown"),
        is_free=bool(data.get("is_free")),
        price=price,
        metacritic_score=metacritic.get("score"),
        platforms=availablePlatforms,
        short_description=data.get("short_description", "").strip(),
        about_the_game_md=html_to_markdown(data.get("about_the_game", "")),
    )


def game_to_markdown(game: GameOverview) -> str:
    lines = [
        f"# {game.name}",
        "",
        f"- **Steam App ID:** {game.appid}",
        f"- **Store page:** {game.store_url}",
        f"- **Developers:** {', '.join(game.developers) or 'Unknown'}",
        f"- **Publishers:** {', '.join(game.publishers) or 'Unknown'}",
        f"- **Genres:** {', '.join(game.genres) or 'Unknown'}",
        f"- **Release date:** {game.release_date}",
        f"- **Price:** {game.price or 'Unknown'}",
        f"- **Platforms** {', '.join(game.platforms) or 'Unknown'}",
        f"- **Metacritic score:** {game.metacritic_score if game.metacritic_score is not None else 'N/A'}",
        "",
        "## Short description",
        "",
        game.short_description,
        "",
        "## About the game",
        "",
        game.about_the_game_md,
        "",
        "---",
        f"*Source: Steam Store API, fetched {date.today().isoformat()}*",
        "",
    ]
    return "\n".join(lines)
