import pytest

from kb_builder.common import slugify, write_markdown


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Hollow Knight", "hollow-knight"),
        ("Baldur's Gate 3", "baldur-s-gate-3"),
        ("Tom Clancy's Rainbow Six® Siege", "tom-clancy-s-rainbow-six-siege"),
        ("Warhammer 40,000: Space Marine 2", "warhammer-40-000-space-marine-2"),
        ("  Dota 2  ", "dota-2"),
        ("--Already--Slugged--", "already-slugged"),
        ("Capcom Co., Ltd.", "capcom-co-ltd"),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_slugify_non_ascii_only_returns_empty():
    assert slugify("原神") == ""


def test_slugify_is_idempotent():
    once = slugify("Marvel's Spider-Man 2")
    assert slugify(once) == once


def test_write_markdown_uses_slug_as_filename(tmp_path):
    path = write_markdown(tmp_path / "games", "No Man's Sky", "# No Man's Sky\n")
    assert path == tmp_path / "games" / "no-man-s-sky.md"
    assert path.read_text(encoding="utf-8") == "# No Man's Sky\n"
