from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

KNOWLEDGE_BASE_DIR = Path(__file__).resolve().parent.parent / "knowledge-base"
GAMES_DIR = KNOWLEDGE_BASE_DIR / "games"
COMPANIES_DIR = KNOWLEDGE_BASE_DIR / "companies"

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

NO_ARTICLE_MARKER = "No Wikipedia article was found"


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
            raw_docs.append(Document(page_content=text, metadata={
                            "name": filepath.stem, "doc_type": doc_type, "source": str(filepath)}))

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
