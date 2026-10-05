import os
from pathlib import Path

from langchain_chroma import Chroma
from langchain_community.document_transformers import LongContextReorder
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

NOTEBOOK_DIR = Path(os.path.abspath(__file__)).parent

COMPANIES_DIR = NOTEBOOK_DIR.parent / "knowledge-base" / "companies"
GAMES_DIR = NOTEBOOK_DIR.parent / "knowledge-base" / "games"
DB_DIR = NOTEBOOK_DIR / "db"

# Retrieval config
FETCH_K = 8
TOP_K = 4

# Embedding model
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Set RAG_REBUILD=1 to force re-embedding even when the stored chunk count matches.
FORCE_REBUILD = os.environ.get("RAG_REBUILD") == "1"

NO_ARTICLE_MARKER = "No Wikipedia article was found"


def load_chunks() -> list[Document]:
    """Read the knowledge base and split it into chunks. Fast and needs no model."""
    raw_docs = []
    skipped_stubs = 0
    for doc_type, directory in (("game", GAMES_DIR), ("company", COMPANIES_DIR)):
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
        chunk_size=1000, chunk_overlap=200)
    chunks = []
    for chunk in splitter.split_documents(raw_docs):
        name = chunk.metadata["name"]
        label = "Game" if chunk.metadata["doc_type"] == "game" else "Company"
        enhanced_content = f"{label} Overview: {name}\n\n{chunk.page_content}"
        chunks.append(Document(page_content=enhanced_content,
                      metadata=chunk.metadata))

    print(f"Created {len(chunks)} enhanced chunks")
    return chunks


def open_or_build_vectorstore(chunks: list[Document], embeddings, force_rebuild: bool = False) -> Chroma:
    """Reuse the persisted store when it matches the knowledge base; otherwise re-embed everything."""
    vectorstore = Chroma(persist_directory=str(DB_DIR),
                         embedding_function=embeddings)
    stored = vectorstore._collection.count()

    if not force_rebuild and stored == len(chunks):
        print(f"Reusing vector store with {stored} chunks from {DB_DIR}")
        return vectorstore

    reason = "forced rebuild" if force_rebuild else f"store has {stored} chunks, knowledge base has {len(chunks)}"
    print(f"Rebuilding vector store ({reason})...")
    vectorstore.delete_collection()
    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=str(DB_DIR),
    )
    print(
        f"Vector store holds {vectorstore._collection.count()} chunks in {DB_DIR}")
    return vectorstore


# Runs once, when this module is first imported (e.g. at server start-up).
chunks = load_chunks()
embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
vectorstore = open_or_build_vectorstore(
    chunks, embeddings, force_rebuild=FORCE_REBUILD)

bm25_retriever = BM25Retriever.from_documents(chunks)
bm25_retriever.k = FETCH_K

vector_retriever = vectorstore.as_retriever(
    search_type="mmr", search_kwargs={"k": TOP_K, "fetch_k": FETCH_K})


def retrieve_from_vectorstore(query: str) -> list[str]:
    """Return the chunk texts most relevant to the query, ordered for the LLM context."""
    docs = vector_retriever.invoke(query)
    print(f"Retrieved {len(docs)} chunks")

    if not docs:
        return []

    # Reorder only after selecting the top chunks, so the best ones are never cut.
    ordered_docs = LongContextReorder().transform_documents(docs)
    return [doc.page_content for doc in ordered_docs]
