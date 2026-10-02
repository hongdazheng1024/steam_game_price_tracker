import argparse
import json
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import httpx

from .build import build_game_by_appid
from .common import KNOWLEDGE_BASE_DIR, REPO_ROOT, TEST_KNOWLEDGE_BASE_DIR
from .steam import iter_appids_by_tag

GENRE_TAGS_PATH = Path(__file__).parent / "data" / "genre_tags.json"
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.toml"
TEST_MODE_DEFAULT_AMOUNT = 5
# Steam's appdetails endpoint allows roughly 200 requests per 5 minutes.
DELAY_BETWEEN_GAMES_SECONDS = 1.5


@dataclass
class Config:
    mode: str
    genres: list[str]
    amount: int | None

    @property
    def kb_dir(self) -> Path:
        return TEST_KNOWLEDGE_BASE_DIR if self.mode == "test" else KNOWLEDGE_BASE_DIR


def load_genre_tags() -> dict[str, int]:
    return json.loads(GENRE_TAGS_PATH.read_text(encoding="utf-8"))


def load_config(path: Path) -> Config:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    genre_tags = load_genre_tags()

    mode = raw.get("mode")
    if mode not in ("test", "prod"):
        raise ValueError(f"'mode' must be \"test\" or \"prod\", got {mode!r}")

    genres = raw.get("genres") or list(genre_tags)
    unknown = [g for g in genres if g not in genre_tags]
    if unknown:
        raise ValueError(
            f"Unknown genre(s) {unknown}. Valid genres: {list(genre_tags)}")

    amount = raw.get("amount")
    if amount is not None and (not isinstance(amount, int) or amount < 1):
        raise ValueError(
            f"'amount' must be a positive integer, got {amount!r}")
    if amount is None and mode == "test":
        amount = TEST_MODE_DEFAULT_AMOUNT

    return Config(mode=mode, genres=genres, amount=amount)


def run(config: Config) -> None:
    genre_tags = load_genre_tags()
    seen_appids: set[int] = set()
    processed = failed = 0

    print(
        f"mode={config.mode} genres={config.genres} amount={config.amount or 'all'} -> {config.kb_dir}")

    for genre in config.genres:
        print(f"\n=== {genre} ===")
        genre_count = 0
        for appid in iter_appids_by_tag(genre_tags[genre]):
            if config.amount is not None and genre_count >= config.amount:
                break
            if appid in seen_appids:
                continue
            seen_appids.add(appid)
            genre_count += 1
            try:
                if build_game_by_appid(appid, config.kb_dir):
                    processed += 1
                else:
                    failed += 1
            except httpx.HTTPError as exc:
                failed += 1
                print(f"  [error] appid {appid}: {exc}")
            time.sleep(DELAY_BETWEEN_GAMES_SECONDS)

    print(f"\nDone. {processed} games written, {failed} failed/skipped.")


def main():
    parser = argparse.ArgumentParser(
        description="Batch-build the knowledge base by Steam genre.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH,
                        help="Path to the TOML config file")
    args = parser.parse_args()
    run(load_config(args.config))


if __name__ == "__main__":
    main()
