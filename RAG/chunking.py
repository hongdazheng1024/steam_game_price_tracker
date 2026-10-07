import re
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

KNOWLEDGE_BASE_DIR = Path(__file__).resolve().parent.parent / "knowledge-base"
GAMES_DIR = KNOWLEDGE_BASE_DIR / "games"
COMPANIES_DIR = KNOWLEDGE_BASE_DIR / "companies"

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

NO_ARTICLE_MARKER = "No Wikipedia article was found"

# Bump when chunk metadata changes, so data_load.py re-embeds instead of reusing stale chunks.
SCHEMA_VERSION = 2

TITLE_PATTERN = re.compile(r"^# (.+)$", re.MULTILINE)
# Header fields as kb_builder writes them, e.g. "- **Steam App ID:** 1245620"
HEADER_FIELDS = {
    "steam_appid": "Steam App ID",
    "developers": "Developers",
    "publishers": "Publishers",
}


def parse_game_header(text: str) -> dict:
    """Read the title, Steam App ID, developers and publishers from a game file's header.

    Fields missing from the file are left out, since Chroma can't store None."""
    # The header ends at the first section, so body text can't be mistaken for a field
    header = text.split("\n## ", 1)[0]
    metadata = {}
    if match := TITLE_PATTERN.search(header):
        metadata["title"] = match.group(1).strip()
    for key, label in HEADER_FIELDS.items():
        if match := re.search(rf"^- \*\*{re.escape(label)}:\*\* (.+)$", header, re.MULTILINE):
            metadata[key] = match.group(1).strip()
    if metadata.get("steam_appid", "").isdigit():
        metadata["steam_appid"] = int(metadata["steam_appid"])
    else:
        metadata.pop("steam_appid", None)
    return metadata


def load_chunks(games_dir: Path = GAMES_DIR, companies_dir: Path = COMPANIES_DIR) -> list[Document]:
    """Read the knowledge base and split it into chunks. Fast and needs no model."""
    raw_docs = []
    skipped_stubs = 0
    for doc_type, directory in (("game", games_dir), ("company", companies_dir)):
        for filepath in directory.glob("**/*.md"):
            text = filepath.read_text(encoding="utf-8")
            if doc_type == "company" and NO_ARTICLE_MARKER in text:
                skipped_stubs += 1
                continue
            metadata = {"name": filepath.stem, "doc_type": doc_type,
                        "source": str(filepath), "schema_version": SCHEMA_VERSION}
            if doc_type == "game":
                # Only the first chunk holds the header, so copy its fields onto every chunk
                metadata |= parse_game_header(text)
            raw_docs.append(Document(page_content=text, metadata=metadata))

    print(
        f"Loaded {len(raw_docs)} documents ({skipped_stubs} company stubs skipped)")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    chunks = []
    for chunk in splitter.split_documents(raw_docs):
        name = chunk.metadata["name"]
        label = "Game" if chunk.metadata["doc_type"] == "game" else "Company"
        enhanced_content = f"{label} Overview: {name}\n\n{chunk.page_content}"
        chunks.append(Document(page_content=enhanced_content,
                      metadata=chunk.metadata))

    print(f"Created {len(chunks)} enhanced chunks")
    return chunks
