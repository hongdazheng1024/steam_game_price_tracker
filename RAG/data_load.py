import os
from pathlib import Path

from langchain_chroma import Chroma
from langchain_community.document_transformers import LongContextReorder
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

from chunking import load_chunks

NOTEBOOK_DIR = Path(os.path.abspath(__file__)).parent

DB_DIR = NOTEBOOK_DIR / "db"

# Retrieval config
FETCH_K = 8
TOP_K = 4

# Embedding model
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Set RAG_REBUILD=1 to force re-embedding even when the stored chunk count matches.
FORCE_REBUILD = os.environ.get("RAG_REBUILD") == "1"


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
