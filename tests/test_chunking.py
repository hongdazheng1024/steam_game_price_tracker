import pytest

from chunking import CHUNK_OVERLAP, CHUNK_SIZE, NO_ARTICLE_MARKER, SCHEMA_VERSION, load_chunks, parse_game_header


@pytest.fixture
def kb(tmp_path):
    games = tmp_path / "games"
    companies = tmp_path / "companies"
    games.mkdir()
    companies.mkdir()
    return games, companies


def test_short_files_become_one_labeled_chunk_each(kb):
    games, companies = kb
    (games / "hollow-knight.md").write_text("# Hollow Knight\n\nA metroidvania.", encoding="utf-8")
    (companies / "team-cherry.md").write_text("# Team Cherry\n\nAn Australian studio.", encoding="utf-8")

    chunks = {c.metadata["name"]: c for c in load_chunks(games, companies)}

    assert set(chunks) == {"hollow-knight", "team-cherry"}
    assert chunks["hollow-knight"].page_content == "Game Overview: hollow-knight\n\n# Hollow Knight\n\nA metroidvania."
    assert chunks["team-cherry"].page_content.startswith("Company Overview: team-cherry\n\n")
    assert chunks["hollow-knight"].metadata["doc_type"] == "game"
    assert chunks["team-cherry"].metadata["doc_type"] == "company"
    assert chunks["team-cherry"].metadata["source"] == str(companies / "team-cherry.md")


def test_company_stubs_are_skipped(kb):
    games, companies = kb
    (companies / "tiny-studio.md").write_text(f"# Tiny Studio\n\n{NO_ARTICLE_MARKER}.", encoding="utf-8")
    assert load_chunks(games, companies) == []


def test_stub_marker_in_game_file_is_kept(kb):
    games, companies = kb
    (games / "odd.md").write_text(f"# Odd\n\n{NO_ARTICLE_MARKER}.", encoding="utf-8")
    assert len(load_chunks(games, companies)) == 1


def test_long_files_are_split_with_overlap(kb):
    games, companies = kb
    text = " ".join(f"word{i}" for i in range(800))
    (games / "long-game.md").write_text(text, encoding="utf-8")

    chunks = load_chunks(games, companies)
    prefix = "Game Overview: long-game\n\n"
    bodies = [c.page_content.removeprefix(prefix) for c in chunks]

    assert len(chunks) > 1
    assert all(c.page_content.startswith(prefix) for c in chunks)
    assert all(len(body) <= CHUNK_SIZE for body in bodies)
    # Neighbouring chunks share text, so context isn't lost at the boundary.
    for previous, current in zip(bodies, bodies[1:]):
        assert current[:50] in previous[-CHUNK_OVERLAP:]
    # Every word survives the split.
    assert set(" ".join(bodies).split()) == set(text.split())


def test_nested_directories_are_included(kb):
    games, companies = kb
    (games / "sub").mkdir()
    (games / "sub" / "nested.md").write_text("Nested game.", encoding="utf-8")
    assert [c.metadata["name"] for c in load_chunks(games, companies)] == ["nested"]


GAME_HEADER = """# ELDEN RING

- **Steam App ID:** 1245620
- **Developers:** FromSoftware, Inc.
- **Publishers:** FromSoftware, Inc., Bandai Namco Entertainment
- **Genres:** Action, RPG

## About the game

- **Publishers:** not a header field
"""


def test_game_header_is_parsed():
    assert parse_game_header(GAME_HEADER) == {
        "title": "ELDEN RING",
        "steam_appid": 1245620,
        "developers": "FromSoftware, Inc.",
        "publishers": "FromSoftware, Inc., Bandai Namco Entertainment",
    }


def test_missing_header_fields_are_left_out():
    assert parse_game_header("# Odd\n\n- **Steam App ID:** N/A\n") == {"title": "Odd"}


def test_game_header_is_copied_to_every_chunk(kb):
    games, companies = kb
    body = " ".join(f"word{i}" for i in range(800))
    (games / "elden-ring.md").write_text(GAME_HEADER + body, encoding="utf-8")
    (companies / "fromsoftware.md").write_text("# FromSoftware\n\n- **Publishers:** x", encoding="utf-8")

    chunks = load_chunks(games, companies)
    game_chunks = [c for c in chunks if c.metadata["doc_type"] == "game"]
    company_chunk = next(c for c in chunks if c.metadata["doc_type"] == "company")

    assert len(game_chunks) > 1
    assert all(c.metadata["steam_appid"] == 1245620 for c in game_chunks)
    assert all(c.metadata["title"] == "ELDEN RING" for c in game_chunks)
    assert "publishers" not in company_chunk.metadata
    assert all(c.metadata["schema_version"] == SCHEMA_VERSION for c in chunks)
