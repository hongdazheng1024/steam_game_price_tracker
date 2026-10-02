import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openai import OpenAI
import chromadb
from langchain_chroma import Chroma
from langchain_community.document_transformers import LongContextReorder

load_dotenv(override=True)

MODEL = "ollama/llama3.1:8b"
MODEL_URL = "http://localhost:11434/v1"
API_KEY = os.environ.get("MODEL_API_KEY")
print(API_KEY)

NOTEBOOK_DIR = Path(os.path.abspath(__file__)
                    ).parent if "__file__" in globals() else Path.cwd()

COMPANIES_DIR = NOTEBOOK_DIR.parents[0] / "knowledge-base" / "companies"
GAMES_DIR = NOTEBOOK_DIR.parents[0] / "knowledge-base" / "games"
DB_DIR = NOTEBOOK_DIR / "db"

# Retrieval config
FETCH_K = 20
TOP_K = 4

# Embedding model
EMBEDDING_MODEL = "bge-large"

ollama = OpenAI(base_url=MODEL_URL, api_key=API_KEY)

print(COMPANIES_DIR)
print(GAMES_DIR)
print(DB_DIR)

# Load and chunk the knowledge base with Entity-Aware Chunking
NO_ARTICLE_MARKER = "No Wikipedia article was found"

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

splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
docs = splitter.split_documents(raw_docs)

chunks = []
for chunk in docs:
    name = chunk.metadata["name"]
    label = "Game" if chunk.metadata["doc_type"] == "game" else "Company"
    enhanced_content = f"{label} Overview: {name}\n\n{chunk.page_content}"
    chunks.append(Document(page_content=enhanced_content,
                  metadata=chunk.metadata))

print(f"Created {len(chunks)} enhanced chunks")

# --- 1. Initial Setup & Embedding Configuration ---
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

# --- 2. Vector Store ---
# Rebuild from scratch each run so re-running doesn't duplicate chunks.
if DB_DIR.exists():
    Chroma(persist_directory=str(DB_DIR),
           embedding_function=embeddings).delete_collection()

vectorstore = Chroma.from_documents(
    documents=chunks,
    embedding=embeddings,
    persist_directory=str(DB_DIR),
)
print(
    f"Vector store holds {vectorstore._collection.count()} chunks in {DB_DIR}")
