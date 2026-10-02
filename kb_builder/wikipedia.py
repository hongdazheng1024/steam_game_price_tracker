from dataclasses import dataclass
from datetime import date

from .common import http_client

SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
SEARCH_URL = "https://en.wikipedia.org/w/api.php"

RELEVANT_KEYWORDS = (
    "game",
    "games",
    "studio",
    "software",
    "publisher",
    "developer",
    "entertainment",
    "interactive",
)


@dataclass
class CompanyOverview:
    name: str
    title: str
    description: str
    extract: str
    page_url: str


def _fetch_summary(client, title: str) -> dict | None:
    resp = client.get(SUMMARY_URL.format(title=title.replace(" ", "_")))
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _search_candidates(client, name: str) -> list[str]:
    resp = client.get(
        SEARCH_URL,
        params={
            "action": "query",
            "list": "search",
            "srsearch": f"{name} video game company",
            "format": "json",
            "srlimit": 5,
        },
    )
    resp.raise_for_status()
    return [item["title"] for item in resp.json().get("query", {}).get("search", [])]


def _looks_relevant(summary: dict) -> bool:
    text = f"{summary.get('description', '')} {summary.get('extract', '')}".lower()
    return any(keyword in text for keyword in RELEVANT_KEYWORDS)


def _title_matches_name(title: str, name: str) -> bool:
    # Accept exact matches and redirects that only add/drop trailing words, e.g.
    # "CD Projekt Red" -> "CD Projekt", or "Valve" -> "Valve Corporation". Reject
    # titles that don't share the name's leading word at all.
    bare_title = title.split(" (")[0].strip().lower()
    title_words = bare_title.split()
    name_words = name.strip().lower().split()
    if not title_words or not name_words:
        return False
    if title_words[0] != name_words[0]:
        return False
    shorter, longer = sorted([title_words, name_words], key=len)
    return shorter == longer[: len(shorter)]


def _accept(candidate: dict | None, name: str) -> bool:
    return bool(
        candidate
        and candidate.get("type") != "disambiguation"
        and _title_matches_name(candidate["title"], name)
        and _looks_relevant(candidate)
    )


def get_company_overview(name: str) -> CompanyOverview | None:
    with http_client() as client:
        summary = _fetch_summary(client, name)

        if _accept(summary, name):
            chosen = summary
        else:
            chosen = None
            for candidate_title in _search_candidates(client, name):
                candidate = _fetch_summary(client, candidate_title)
                if _accept(candidate, name):
                    chosen = candidate
                    break

        if not chosen or not chosen.get("extract"):
            return None

        return CompanyOverview(
            name=name,
            title=chosen["title"],
            description=chosen.get("description", ""),
            extract=chosen["extract"],
            page_url=chosen.get("content_urls", {}).get("desktop", {}).get("page", ""),
        )


def company_to_markdown(company: CompanyOverview | None, fallback_name: str) -> str:
    if company is None:
        return "\n".join(
            [
                f"# {fallback_name}",
                "",
                "No Wikipedia article was found for this company.",
                "",
                "---",
                f"*Source: Wikipedia REST API, fetched {date.today().isoformat()}*",
                "",
            ]
        )

    lines = [
        f"# {company.title}",
        "",
        f"- **Wikipedia page:** {company.page_url}",
    ]
    if company.description:
        lines.append(f"- **Description:** {company.description}")
    lines += [
        "",
        "## Overview",
        "",
        company.extract,
        "",
        "---",
        f"*Source: Wikipedia REST API, fetched {date.today().isoformat()}*",
        "",
    ]
    return "\n".join(lines)
