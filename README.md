# Steam Game Price Tracker

[![Tests](https://github.com/hongdazheng1024/steam_game_price_tracker/actions/workflows/tests.yml/badge.svg)](https://github.com/hongdazheng1024/steam_game_price_tracker/actions/workflows/tests.yml)

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
| Price data from IsThereAnyDeal (cached in SQLite)                | Done    |
| Price-tracking agent (Llama 3.1 tool calling, Gradio chat UI)    | Done    |
| Buy-or-wait recommendations                                      | Done    |
| Target-price prediction (price history + RAG context)            | Planned |
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
- Every chunk has `name`, `doc_type` (`game` or `company`), `source` and `schema_version` metadata.
- Game chunks also carry `title`, `steam_appid`, `developers` and `publishers`, read from the game file's header. Only the first chunk contains the header, so these are copied onto every chunk of the game. Fields missing from the file are left out.
- Company stub files (studios with no Wikipedia article) are skipped because they contain only a name.
- On start-up the existing store is reused when its chunk count and `schema_version` match the knowledge base. Otherwise it is rebuilt, so adding games or changing the chunk metadata (bump `SCHEMA_VERSION` in [chunking.py](RAG/chunking.py)) triggers a rebuild automatically. Edits to existing files that don't change the chunk count need `RAG_REBUILD=1`.
- `retrieve_from_vectorstore(query)` returns the 4 most relevant chunks. It uses MMR to avoid several near-identical chunks from one game, then orders them for the LLM context.
- The first run downloads the embedding model from Hugging Face.

### Finding games from a description

`find_game_candidates(query)` finds the games a user may mean when they describe a game instead of naming it, like "the new Capcom samurai game" or "FromSoftware's souls game". It returns up to 5 games, best first, each as `{"name", "title", "steam_appid"}`, or an empty list when nothing matches well enough (the user should then be asked for the exact title).

- If the query names a developer or publisher, only that company's games are ranked. Embeddings match company names poorly (Elden Ring ranked below unrelated games for "FromSoftware souls game"), so the company match comes from the game headers instead.
  - Company names are compared without case, punctuation or corporate suffixes, so "Capcom" matches "CAPCOM Co., Ltd.". They must match as whole words.
  - Commas that belong to a name are handled: "FromSoftware, Inc., Bandai Namco Entertainment" is two companies.
  - There are no aliases yet, so "EA" doesn't match "Electronic Arts".
- Otherwise every game is ranked, and company files are left out.
- Each game is scored by its best-matching chunk (relevance from about -0.2 for unrelated text to 0.6 for a clear match). Without a company match, nothing is returned when the best game scores under 0.2. Games more than 0.15 behind the best one are dropped.
- The thresholds are set in [data_load.py](RAG/data_load.py) (`MIN_CANDIDATE_SCORE`, `MAX_CANDIDATE_GAP`, `MAX_CANDIDATES`). The matching and ranking logic is in [candidates.py](RAG/candidates.py), which needs no ML stack, so it is unit-tested.

| Query                       | Candidates                                                                  |
| --------------------------- | --------------------------------------------------------------------------- |
| the new Capcom samurai game | Onimusha: Way of the Sword                                                  |
| that Pragmata thing         | PRAGMATA                                                                    |
| Capcom fighting game        | Street Fighter V, Street Fighter 6                                          |
| FromSoftware souls game     | ELDEN RING                                                                  |
| Ubisoft's pirate game       | Ubisoft's games in the knowledge base (Skull and Bones isn't in it yet)     |
| best pizza recipe           | none                                                                        |

The agent doesn't call this yet. The next step is mapping each candidate's Steam App ID to its IsThereAnyDeal id and showing the candidates as buttons, the same way `search_game` shows its choices.

## Price-tracking agent

[agent/steam_price_tracker.ipynb](agent/steam_price_tracker.ipynb) is a chat assistant for Steam game prices. Ask about a game and Llama 3.1 calls the `search_game` tool to find it on IsThereAnyDeal, then compares the current deal with the regular price and the all-time low and says whether to buy now or wait.

- The model only calls the tool when you name a specific game. Greetings and vague questions get a normal reply.
- The country (US, JP, GB, CA, AU, DE, FR) is picked in the UI.
- Both lookups are cached in a local SQLite database (`agent/steam_tracker.db`, git-ignored), so repeat questions make fewer API calls, or none.

### Finding the game

`search_game` turns the title you typed into an IsThereAnyDeal game id before fetching prices:

1. It normalizes the title (lowercase, punctuation removed), so "clair obscur expedition 33" matches "Clair Obscur: Expedition 33".
2. It checks the local `game_catalog` table. One exact title match goes straight to the price, with no API search.
3. Titles that only contain your query, or several games with the same title, are returned as a list to choose from.
4. With no local match, it calls the IsThereAnyDeal search API and stores every result in `game_catalog`. One exact match among the results goes straight to the price; otherwise the results are returned as a list.

When there's a list, the assistant asks which one you mean and shows each game as a button under its message (Gradio chatbot options). Click one, and it looks up that game's price and summarizes it:

- Each button holds the game's id, so two games with the same title can't be mixed up. Repeated titles are numbered, like "Prey (1)" and "Prey (2)".
- When the list came from the local catalog, a "None of these, search online" button is added. The catalog only holds games from earlier searches, so this runs the IsThereAnyDeal search (`search_game` with `search_online=True`) and shows its results instead.
- The list is written by the code, not the model, so it always matches the choices `search_game` returned.
- Only the latest list's buttons stay clickable. You can also type a game's title instead of clicking.

Prices are then cached for 24 hours per game and country, so asking about the same game again needs no API calls at all.

### Prerequisites

1. Get an API key from [IsThereAnyDeal](https://isthereanydeal.com/apps/my/).
2. Make sure Ollama is running and `llama3.1` is pulled (see [Setup](#setup)).
3. Add both keys to a `.env` file in the repo root. Ollama doesn't check the model key, but it must be set:

   ```
   ITAD-API-KEY=your-isthereanydeal-key
   MODEL_API_KEY=ollama
   ```

4. Create the database (see [Database setup](#database-setup)).

### Database setup

[agent/steam_price_db_init.py](agent/steam_price_db_init.py) creates the SQLite database `steam_tracker.db` (git-ignored) in the directory you run it from. Run it from `agent/`:

```bash
cd agent
uv run python steam_price_db_init.py
```

It creates two tables:

| Table          | Contents                                                                                                                                                    |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `game_library` | Cached price data (current deal and all-time low) per game and country, with the time it was fetched.                                                       |
| `game_catalog` | IsThereAnyDeal game ids with their official titles, plus a normalized title (lowercase, no punctuation) for matching. Filled from search results.            |

Running the script again is safe: tables are only created if they don't exist, so cached data is kept. If you already have a `steam_tracker.db` from before `game_catalog` existed, run the script once to add it. This also means schema changes don't apply to an existing database unless you drop the tables first.

Settings in [agent/db_config.toml](agent/db_config.toml):

| Field                  | Required | Description                                                                                                                                                                      |
| ---------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `force_to_drop_tables` | No       | `true` drops `game_library` and `game_catalog` before recreating them, deleting all cached data. Use it after a schema change, then set it back to `false`. Defaults to `false`. |

The script stops with an error if the config file is missing or `force_to_drop_tables` isn't `true` or `false`.

The older notebook [agent/steam_price_db_init.ipynb](agent/steam_price_db_init.ipynb) still creates only `game_library`. It drops the table on every run and can add a sample game.

### Running

Open [agent/steam_price_tracker.ipynb](agent/steam_price_tracker.ipynb) with the project's `.venv` as the kernel (for example in VS Code), or start Jupyter with:

```bash
uv run --with jupyter jupyter lab
```

Run all cells. The last cell starts the Gradio chat UI, at http://127.0.0.1:7860 by default.

## Tests

```bash
uv run pytest
```

The tests cover `slugify`, the HTTP retry/backoff logic (with the network and `time.sleep` mocked out), knowledge-base chunking (including game header metadata) and the company matching and ranking behind `find_game_candidates`. They need none of the ML stack, so [GitHub Actions](.github/workflows/tests.yml) runs them on every push and pull request with only the `dev` dependency group (`uv run --only-group dev pytest`).

## Project layout

```
kb_builder/
├── batch.py        config-driven batch job
├── build.py        build one game and its companies
├── steam.py        Steam search, appdetails, HTML to Markdown
├── wikipedia.py    company lookup via the Wikipedia API
├── common.py       paths, throttled HTTP client, helpers
└── data/genre_tags.json
agent/
├── steam_price_tracker.ipynb   price tool, Llama 3.1 agent and Gradio chat UI
├── steam_price_db_init.py      creates the SQLite price cache and game catalog
├── db_config.toml              database init settings
└── steam_price_db_init.ipynb   older notebook version of the database init, with sample data
RAG/
├── chunking.py     reads the knowledge base and splits it into chunks, with game header metadata
├── candidates.py   company matching and ranking for find_game_candidates (no ML dependencies)
├── data_load.py    builds/opens the Chroma vector store and exposes retrieval and find_game_candidates
└── db/             generated vector store (git-ignored)
tests/              pytest suite
.github/workflows/  CI that runs the tests on every push
config.toml         batch job settings
main.py             Ollama smoke test
```
