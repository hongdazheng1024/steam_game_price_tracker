"""Matching and ranking for find_game_candidates in data_load.py.

Kept free of the embedding model and Chroma, so the tests can run without the ML stack."""
import re

# Trailing words that don't tell companies apart: "CAPCOM Co., Ltd." and "Capcom" are the same company
CORPORATE_SUFFIXES = {"co", "ltd", "limited", "inc", "llc", "corp", "corporation", "gmbh", "plc", "sa", "ab"}


def normalize_words(text: str) -> list[str]:
    # Same normalization as normalize_title in the notebook: "Ubisoft's" -> ["ubisoft", "s"]
    return re.sub(r"[\W_]+", " ", text.casefold()).split()


def company_key(name: str) -> str:
    """Lowercase company name without corporate suffixes, e.g. "CAPCOM Co., Ltd." -> "capcom"."""
    words = normalize_words(name)
    while words and words[-1] in CORPORATE_SUFFIXES:
        words.pop()
    return " ".join(words)


def split_companies(field: str) -> list[str]:
    """Split a "Publishers" field into company names.

    Commas separate companies but also appear inside names, e.g.
    "FromSoftware, Inc., Bandai Namco Entertainment" -> ["FromSoftware, Inc.", "Bandai Namco Entertainment"]."""
    companies = []
    for part in (p.strip() for p in field.split(",")):
        if not part:
            continue
        # A part that is only a suffix ("Inc.", "Ltd.") belongs to the company before it
        if companies and not company_key(part):
            companies[-1] += f", {part}"
        else:
            companies.append(part)
    return companies


def index_company_games(game_metadatas: list[dict]) -> dict[str, set[str]]:
    """Map each developer or publisher key to the names of its games."""
    index = {}
    for metadata in game_metadatas:
        for field in ("developers", "publishers"):
            for company in split_companies(metadata.get(field, "")):
                if key := company_key(company):
                    index.setdefault(key, set()).add(metadata["name"])
    return index


def match_company_games(query: str, company_games: dict[str, set[str]]) -> set[str]:
    """Names of the games made or published by every company the query mentions."""
    # Pad with spaces so a key only matches whole words: "2k" mustn't match "2k25"
    padded_query = f" {' '.join(normalize_words(query))} "
    names = set()
    for key, games in company_games.items():
        if f" {key} " in padded_query:
            names |= games
    return names


def rank_candidates(scored: list[tuple[dict, float]], min_score: float | None,
                    max_gap: float, max_results: int) -> list[dict]:
    """Turn (chunk metadata, relevance score) pairs into games, best first.

    A game keeps its best chunk's score. Nothing is returned when the best game scores below
    min_score (None skips that check), and games more than max_gap behind the best are dropped."""
    best = {}
    for metadata, score in scored:
        name = metadata["name"]
        if name not in best or score > best[name][1]:
            best[name] = (metadata, score)

    ranked = sorted(best.values(), key=lambda pair: pair[1], reverse=True)
    if not ranked or (min_score is not None and ranked[0][1] < min_score):
        return []

    top_score = ranked[0][1]
    return [
        {
            "name": metadata["name"],
            "title": metadata.get("title", metadata["name"]),
            "steam_appid": metadata.get("steam_appid"),
        }
        for metadata, score in ranked[:max_results]
        if score >= top_score - max_gap
    ]
