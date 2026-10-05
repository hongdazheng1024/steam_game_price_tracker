# Steam Game Price Tracker

An LLM-powered assistant that helps you decide whether a Steam game is worth buying, and when to buy it.

Given a game, the assistant combines current and historical price data with background knowledge about the game and its developer/publisher to answer questions like:

- Is this game worth its current price?
- Should I buy now or wait for a sale?
- How long until the price is likely to drop to my target price?

## How it works

| Component                  | Choice                                                                                                                                         |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| LLM                        | Llama 3.1 8B, run locally through [Ollama](https://ollama.com)                                                                                 |
| Price data                 | [IsThereAnyDeal](https://isthereanydeal.com) API                                                                                               |
| Game and company knowledge | Steam Store API and Wikipedia, stored as Markdown files                                                                                        |
| Retrieval (RAG)            | [Chroma](https://www.trychroma.com) vector store built from the game and company overviews, embedded with `all-MiniLM-L6-v2` through LangChain |
| Tooling                    | Python 3.12, [uv](https://docs.astral.sh/uv/)                                                                                                  |

The vector library gives the LLM context a price history can't: a game's genre, release date, reviews and publisher, plus the studio's track record. That context is meant to make price predictions, and the estimated time until a price hits your target, more accurate than looking at prices alone.

## Status

| Stage                                                            | State   |
| ---------------------------------------------------------------- | ------- |
| Knowledge base builder (game and company overviews to Markdown)  | Done    |
| Vector library (RAG) over the knowledge base                     | Done    |
| Price data from IsThereAnyDeal                                   | Planned |
| Buy-or-wait recommendations and target-price prediction          | Planned |
| Python ML service (RAG + Llama 3.1) exposed over HTTP            | Planned |
| Spring Boot backend (users, preferences, track list, price jobs) | Planned |
| Next.js chat frontend                                            | Planned |

## Planned architecture

The frontend and backend are built after the ML pieces above.

| Layer      | Technology        | Responsibility                                                                                                                                                                               |
| ---------- | ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Frontend   | Next.js           | Chat interface, user preferences, game track list                                                                                                                                            |
| Backend    | Java, Spring Boot | Authentication, stored chat history, preferences and track list, scheduled price checks and target-price alerts, API for the frontend                                                        |
| ML service | Python            | Runs retrieval over the vector library, lets Llama 3.1 call the IsThereAnyDeal price tool (with a local cache to avoid repeat API calls), summarizes the price history and returns an answer |

```
Next.js ── new message + conversation id ──▶ Spring Boot ──▶ Python ML service
                                              loads history,        RAG retrieval
                                              preferences and       + Llama 3.1
                                              tracked games         (via Ollama)
Next.js ◀────────── streamed reply ──────── Spring Boot ◀────────── streamed reply
```

- The frontend sends only the new message. Spring Boot owns the stored history and preferences and passes them to the ML service with each request.
- The ML service keeps no user state. Its only storage is a cache of price data, which keeps it easy to test and replace.
- Replies are streamed back to the browser, because a local 8B model is slow.
- Target-price alerts run on a schedule in Spring Boot, separate from chat.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and [Ollama](https://ollama.com).

```bash
uv sync
ollama pull llama3.1
```

Check that the model responds:

```bash
uv run main.py
```

## Building the knowledge base

The knowledge base is a folder of Markdown files:

```
knowledge-base/
├── games/       one file per game (Steam Store API)
└── companies/   one file per developer/publisher (Wikipedia)
```

A company only gets a file once. Studios without a Wikipedia article get a short stub file, so they aren't looked up again.

### Batch job

Pulls games by genre from Steam's store search, then writes each game and its companies.

```bash
uv run python -m kb_builder.batch                       # uses config.toml
uv run python -m kb_builder.batch --config other.toml
```

Settings in [config.toml](config.toml):

| Field    | Required | Description                                                                                                                                                        |
| -------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `mode`   | Yes      | `"test"` writes to `knowledge-base-test/` and defaults to 5 games per genre. `"prod"` writes to `knowledge-base/` and processes every game unless `amount` is set. |
| `genres` | No       | Genres to process. Omit for all genres in [genre_tags.json](kb_builder/data/genre_tags.json).                                                                      |
| `amount` | No       | Maximum games per genre.                                                                                                                                           |

Notes:

- Genre names map to Steam tag IDs in [genre_tags.json](kb_builder/data/genre_tags.json).
- Requests are throttled per host and retried on 429/5xx responses. A full genre has tens of thousands of games, so an uncapped prod run takes a very long time. Use `amount` to cap it.
- Game files are rewritten on every run. Existing company files are skipped.

### Single games

```bash
uv run python -m kb_builder.build "Hollow Knight" "Dota 2"
```

## Vector store and retrieval

[RAG/data_load.py](RAG/data_load.py) loads the knowledge base into a Chroma vector store saved in `RAG/db/` (git-ignored) and exposes retrieval. It runs once, when the module is first imported (for example at server start-up), and it can also be run directly to build or refresh the store:

```bash
uv run python RAG/data_load.py                  # reuse the store if it is up to date
RAG_REBUILD=1 uv run python RAG/data_load.py    # force a full re-embed
```

- Game and company files are split into chunks of about 1,000 characters with 200 characters of overlap. Each chunk is prefixed with "Game Overview: <name>" or "Company Overview: <name>".
- Every chunk has `name`, `doc_type` (`game` or `company`) and `source` metadata.
- Company stub files (studios with no Wikipedia article) are skipped because they contain only a name.
- On start-up the existing store is reused when its chunk count matches the knowledge base. Otherwise it is rebuilt, so adding games triggers a rebuild automatically. Edits to existing files that don't change the chunk count need `RAG_REBUILD=1`.
- `retrieve_from_vectorstore(query)` returns the 4 most relevant chunks. It uses MMR to avoid several near-identical chunks from one game, then orders them for the LLM context.
- The first run downloads the embedding model from Hugging Face.

## Project layout

```
kb_builder/
├── batch.py        config-driven batch job
├── build.py        build one game and its companies
├── steam.py        Steam search, appdetails, HTML to Markdown
├── wikipedia.py    company lookup via the Wikipedia API
├── common.py       paths, throttled HTTP client, helpers
└── data/genre_tags.json
RAG/
├── data_load.py    builds/opens the Chroma vector store and exposes retrieval
└── db/             generated vector store (git-ignored)
config.toml         batch job settings
main.py             Ollama smoke test
```
