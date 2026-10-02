import argparse
import time
from pathlib import Path

from .common import KNOWLEDGE_BASE_DIR, slugify, write_markdown
from .steam import find_appid, game_to_markdown, get_game_overview
from .wikipedia import company_to_markdown, get_company_overview


def build_game_by_appid(appid: int, kb_dir: Path) -> bool:
    game = get_game_overview(appid)
    if game is None:
        print(f"  [skip] could not fetch details for appid {appid}")
        return False

    path = write_markdown(kb_dir / "games", game.name, game_to_markdown(game))
    print(f"  [ok] {game.name} -> {path.relative_to(kb_dir.parent)}")

    for company in set(game.developers + game.publishers):
        if (kb_dir / "companies" / f"{slugify(company)}.md").exists():
            print(f"    [skip] {company} -> already in knowledge base")
            continue
        build_company(company, kb_dir)
    return True


def build_game(game_name: str, kb_dir: Path = KNOWLEDGE_BASE_DIR) -> None:
    match = find_appid(game_name)
    if match is None:
        print(f"  [skip] no Steam match for '{game_name}'")
        return
    build_game_by_appid(match[0], kb_dir)


def build_company(company_name: str, kb_dir: Path) -> None:
    overview = get_company_overview(company_name)
    path = write_markdown(kb_dir / "companies", company_name, company_to_markdown(overview, company_name))
    status = "ok" if overview else "no wiki article"
    print(f"    [{status}] {company_name} -> {path.relative_to(kb_dir.parent)}")


def main():
    parser = argparse.ArgumentParser(description="Scrape Steam game + company overviews into the knowledge base.")
    parser.add_argument("games", nargs="+", help="Game names to look up on Steam")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds to wait between network calls")
    args = parser.parse_args()

    for game_name in args.games:
        print(f"{game_name}:")
        build_game(game_name)
        time.sleep(args.delay)


if __name__ == "__main__":
    main()
