import os
import warnings
from pathlib import Path

from langchain_chroma import Chroma
from langchain_community.document_transformers import LongContextReorder
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

from candidates import index_company_games, match_company_games, rank_candidates
from chunking import SCHEMA_VERSION, load_chunks

NOTEBOOK_DIR = Path(os.path.abspath(__file__)).parent

DB_DIR = NOTEBOOK_DIR / "db"

# Retrieval config
FETCH_K = 8
TOP_K = 4

# find_game_candidates config: relevance scores run from about -0.2 (unrelated) to 0.6 (clear match)
CANDIDATE_FETCH_K = 20
MIN_CANDIDATE_SCORE = 0.2
MAX_CANDIDATE_GAP = 0.15
MAX_CANDIDATES = 5

# Embedding model
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Set RAG_REBUILD=1 to force re-embedding even when the stored chunk count matches.
FORCE_REBUILD = os.environ.get("RAG_REBUILD") == "1"


def open_or_build_vectorstore(chunks: list[Document], embeddings, force_rebuild: bool = False) -> Chroma:
    """Reuse the persisted store when it matches the knowledge base; otherwise re-embed everything."""
    vectorstore = Chroma(persist_directory=str(DB_DIR),
                         embedding_function=embeddings)
    stored = vectorstore._collection.count()
    sample = vectorstore._collection.get(limit=1, include=["metadatas"])["metadatas"]
    stored_version = sample[0].get("schema_version") if sample else None

    if not force_rebuild and stored == len(chunks) and stored_version == SCHEMA_VERSION:
        print(f"Reusing vector store with {stored} chunks from {DB_DIR}")
        return vectorstore

    if force_rebuild:
        reason = "forced rebuild"
    elif stored_version != SCHEMA_VERSION:
        reason = f"store has chunk schema {stored_version}, knowledge base has {SCHEMA_VERSION}"
    else:
        reason = f"store has {stored} chunks, knowledge base has {len(chunks)}"
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


# Developer/publisher -> game names, so a query naming a company can be limited to its games
company_games = index_company_games(
    [chunk.metadata for chunk in chunks if chunk.metadata["doc_type"] == "game"])


def scored_search(query: str, k: int, filter: dict) -> list:
    """(Document, relevance score) pairs. Unrelated chunks score below 0, which rank_candidates
    handles, so LangChain's warning about it (which prints every result) is silenced."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Relevance scores must be between 0 and 1")
        return vectorstore.similarity_search_with_relevance_scores(query, k=k, filter=filter)


def find_game_candidates(query: str) -> list[dict]:
    """Games the user may mean by a description such as "the new Capcom samurai game", best first.

    Each is {"name", "title", "steam_appid"}. Empty when nothing matches well enough,
    in which case the user should be asked for the exact title."""
    names = match_company_games(query, company_games)
    if names:
        # The query names a company: rank only its games, since embeddings match company names poorly.
        # The company match is the evidence, so there's no minimum score.
        n_chunks = sum(1 for chunk in chunks if chunk.metadata["name"] in names)
        results = scored_search(query, k=n_chunks, filter={"name": {"$in": sorted(names)}})
        min_score = None
    else:
        results = scored_search(query, k=CANDIDATE_FETCH_K, filter={"doc_type": "game"})
        min_score = MIN_CANDIDATE_SCORE

    candidates = rank_candidates([(doc.metadata, score) for doc, score in results],
                                 min_score, MAX_CANDIDATE_GAP, MAX_CANDIDATES)
    print(f"Found {len(candidates)} game candidates for '{query}'")
    return candidates
