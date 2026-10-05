import pytest

from chunking import CHUNK_OVERLAP, CHUNK_SIZE, NO_ARTICLE_MARKER, load_chunks


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
