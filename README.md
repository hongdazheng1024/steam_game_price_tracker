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
| Target-price prediction (price history + RAG context)            | Done    |
| Python ML service (RAG + Llama 3.1) exposed over HTTP            | Done    |
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

| Query                       | Candidates                                                              |
| --------------------------- | ----------------------------------------------------------------------- |
| the new Capcom samurai game | Onimusha: Way of the Sword                                              |
| that Pragmata thing         | PRAGMATA                                                                |
| Capcom fighting game        | Street Fighter V, Street Fighter 6                                      |
| FromSoftware souls game     | ELDEN RING                                                              |
| Ubisoft's pirate game       | Ubisoft's games in the knowledge base (Skull and Bones isn't in it yet) |
| best pizza recipe           | none                                                                    |

The price-tracking agent calls it through its `find_game_candidates` tool (see [Finding a game from a description](#finding-a-game-from-a-description)).

## Price-tracking agent

[agent/tracker.py](agent/tracker.py) is a chat assistant for Steam game prices, served over HTTP by [agent/api.py](agent/api.py) (see [ML service API](#ml-service-api)) and in a local Gradio UI by [agent/gradio_app.py](agent/gradio_app.py). Ask about a game and Llama 3.1 finds it on IsThereAnyDeal, then compares the current deal with the regular price and the all-time low and says whether to buy now or wait.

- The model has two tools: `search_game` when you name a game (even a partial or misspelled title), and `find_game_candidates` when you describe one without naming it, by publisher, genre, plot or "the new X game". The system prompt has examples of each.
- Greetings and general questions are meant to get a normal reply, but Llama 3.1 8B often calls `search_game` with an empty or made-up title anyway.
- The country (US, JP, GB, CA, AU, DE, FR) comes with each request (in the Gradio UI, from a dropdown).
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

### Finding a game from a description

For "the new Capcom samurai game" or "the game where you play as a knight in a bug kingdom", the `find_game_candidates` tool searches the knowledge base with [`find_game_candidates`](#finding-games-from-a-description) and always shows the results as buttons, even when there's only one, since a description is never certain:

1. The knowledge base returns up to 5 games with their Steam App IDs.
2. Each Steam App ID is mapped to an IsThereAnyDeal id. Mappings already in `game_catalog` (its `steam_appid` column) are reused; the rest are looked up in one IsThereAnyDeal request (`/lookup/id/shop/61/v1`, where 61 is Steam) and stored. A game already in the catalog from a search keeps its IsThereAnyDeal title.
3. The games are shown as buttons, the same way as `search_game`'s choices. Clicking one looks up its price.
4. A "None of these" button asks for the exact title, without calling the model or any tool. When nothing matches the description at all, the model asks for the exact title instead.

Notes:

- The tool searches the user's message as written, not the description the model passes. The model sometimes rewrites it, or copies one from the prompt's examples.
- If `search_game` finds nothing for a title, the user's message is tried as a description before giving up, in case the model sent a description to the wrong tool.
- Only games in the knowledge base can be found this way. "Ubisoft's pirate game" shows Ubisoft's other games, because Skull and Bones isn't in it yet.

### Predicting when a game hits a target price

When the user asks "when will it drop to $25?", the `predict_target_price` tool takes the game title (the model carries it over from earlier in the conversation) and the target price:

1. The title is resolved like `search_game`'s, except that without an exact catalog match it searches IsThereAnyDeal right away, since the catalog's partial matches may only be the game's DLCs. Several matches come back as choices.
2. The game's price history in the selected country is loaded ([Price history](#price-history)).
3. Only if that history isn't enough (the game isn't at the target and has reached it fewer than twice) does it fetch comparables, since each costs API calls. The candidates are the knowledge base's games from the same publishers, matched on the publisher names IsThereAnyDeal gives (`games_by_companies` in `RAG/data_load.py`), then ranked by [`find_comparables`](#comparable-games).
4. [`predict_target_price`](#target-price-prediction) works out the prediction.
5. The reply is the model's sentence or two followed by the prediction's facts block, as written. The model only sees the headline (the facts block is left out of the tool result it gets) and is held to one or two sentences. `clean_intro` keeps the first paragraph's first two complete sentences, and swaps in a fixed intro ("Here's my estimate for Hades.") if they contain numbers the headline doesn't have, Markdown, talk about the tool or its result, or words like "likely" for a low-confidence prediction.

A reply looks like this:

```
Monster Hunter Wilds might reach $25 around January 2027, though it's hard to call.

**Monster Hunter Wilds → $25.00** (64% off its $69.99 launch price)
- **Now:** $39.99 · all-time low $29.39
- **Projected:** around **Jan 2027**, based on similar games
- **Similar games:**
  - Monster Hunter: World (same publisher and price tier): 64% off at 18 months (Feb 2020, publisher's own sale)
  - Street Fighter V (same publisher and price tier): 64% off at 18 months (Dec 2016, Winter Sale 2016), *not used: release date estimated*
  - Street Fighter 6 (same publisher and price tier): 64% off at 28 months (Sep 2025, publisher's own sale)
- **Sales to watch:** Winter Sale 2026 (Dec 17 – Jan 4); Spring Sale 2027 (Mar 18 – 25)
- **Reception:** mixed (52%) vs 81% for similar games, so an earlier or deeper cut is more likely
- **Regular price:** cut from $69.99 to $39.99 in Aug 2026, 17 months after release
- **Confidence:** low, from 2 similar games with known release dates

*Estimate from past Steam prices, not a guarantee.*
```

A target price that's missing or not a number (the model sometimes sends `0`) makes the model ask the user what they'd pay.

### Prerequisites

1. Get an API key from [IsThereAnyDeal](https://isthereanydeal.com/apps/my/).
2. Make sure Ollama is running and `llama3.1` is pulled (see [Setup](#setup)).
3. Add the key to a `.env` file in the repo root (or set `ITAD_API_KEY` in the environment, for shells and containers that reject the hyphen):

   ```
   ITAD-API-KEY=your-isthereanydeal-key
   ```

   Optional settings: `LLM_BASE_URL` (default `http://localhost:11434/v1`, Ollama's OpenAI-compatible API), `LLM_MODEL` (default `llama3.1`) and `MODEL_API_KEY` (Ollama doesn't check it).

4. Create the database (see [Database setup](#database-setup)).

### Database setup

[agent/steam_price_db_init.py](agent/steam_price_db_init.py) creates the SQLite database `agent/steam_tracker.db` (git-ignored), whichever directory you run it from. The ML service also runs it at start-up, so this is only needed for the Gradio UI or after changing `force_to_drop_tables`:

```bash
uv run python agent/steam_price_db_init.py
```

It creates six tables:

| Table                | Contents                                                                                                                                                                                                                   |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `game_library`       | Cached Steam price data (current deal and all-time low) per game and country, with the time it was fetched.                                                                                                                |
| `game_catalog`       | IsThereAnyDeal game ids with their official titles, plus a normalized title (lowercase, no punctuation) for matching. Filled from search results. Games found from a description also have their `steam_appid`.            |
| `price_history`      | Steam price changes per game and country from IsThereAnyDeal's `/games/history/v2`: timestamp (UTC), price, regular price, discount and currency. Each price holds until the next row.                                     |
| `price_history_sync` | When each game's history was last fetched, per country. A row with no `price_history` rows means the game has no Steam price history.                                                                                      |
| `game_info`          | Details per game from IsThereAnyDeal's `/games/info/v2`: release date, Steam App ID, early access, tags, Steam user score and review count, Metascore, and recent and peak player counts, with the time they were fetched. |
| `game_companies`     | Each game's publishers and developers by IsThereAnyDeal company id. One company can appear under several names ("Capcom", "Capcom Co., Ltd.") but shares one id, so similar games are matched by id.                       |

Running the script again is safe: tables are only created if they don't exist, so cached data is kept. If you already have a `steam_tracker.db` from before `game_catalog` existed, run the script once to add it. Columns added later, like `game_catalog.steam_appid`, are added to an existing table when the script runs, keeping its rows. Other schema changes don't apply to an existing database unless you drop the tables first.

Settings in [agent/db_config.toml](agent/db_config.toml):

| Field                     | Required | Description                                                                                                                                                |
| ------------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `force_to_drop_tables`    | No       | `true` drops every table before recreating them, deleting all cached data. Use it after a schema change, then set it back to `false`. Defaults to `false`. |
| `history_max_age_hours`   | No       | How old a game's cached price history can get before it's refreshed. Defaults to `24`.                                                                     |
| `game_info_max_age_hours` | No       | How old a game's cached details (reviews, player counts, publishers) can get before they're refetched. Defaults to `168` (a week).                         |

The script stops with an error if the config file is missing, `force_to_drop_tables` isn't `true` or `false`, or either max age isn't a positive number.

### Price history

[agent/price_history.py](agent/price_history.py) fetches a game's Steam price history (shop 61) and caches it. `get_price_history` checks `price_history_sync` before calling the API:

1. Never fetched: it fetches the full history. Without `since` the API only returns the last 3 months, so the first fetch asks for everything since 2000.
2. Fetched less than `history_max_age_hours` ago: no API call.
3. Older: it fetches only the changes since the last fetch (starting a day earlier in case IsThereAnyDeal recorded a change late; repeated rows are ignored).

It then returns the history from the database, oldest first. The new rows and the sync time are saved in one transaction after a successful API call, so a failed call leaves the cache as it was.

### Game info

[agent/game_info.py](agent/game_info.py) fetches a game's details and caches them in `game_info` and `game_companies`. `get_game_info` calls the API only when the game was never fetched or its details are older than `game_info_max_age_hours`, and a refresh replaces the details and company list together. It returns `None` for a game IsThereAnyDeal doesn't know. The details will be used to find comparable games for price predictions: same publisher, similar launch price, similar Steam reception.

Reviews and player counts are a snapshot from when they were fetched. The Steam user score can differ a lot from the Metascore: Monster Hunter Wilds and Monster Hunter: World both have a Metascore of 88, but Steam scores of 52 and 89.

### Comparable games

A newly released game has too little price history of its own, so its prices are predicted from comparable games. [agent/comparables.py](agent/comparables.py) finds them with `find_comparables(db_file, game_id, api_key, candidate_ids)`, ranking games released before the target on a ladder:

1. Same publisher and same launch price tier (under $15, $15-30, $30-50, $50+)
2. Same publisher, other or unknown tier, since one publisher may discount a AAA and an indie game differently
3. Another publisher, same tier and at least one shared tag

Within a level, games with the same Steam reception (Steam's labels: positive 80+, mostly positive 70-79, mixed 40-69, negative below 40) come first, then games with more shared tags, then newer games. Publishers are matched by IsThereAnyDeal company id.

- `candidate_ids` are games worth fetching, such as the knowledge base's games from the same publisher. At most 10 are fetched, since each new one costs two API calls (details and history). Games already cached are considered too, at no API cost. A candidate that fails to fetch is skipped.
- The launch price is the first regular price in the game's US history, so tiers are in dollars whatever the user's country. For games IsThereAnyDeal started tracking after release, it's an approximation.
- A game IsThereAnyDeal has no release date for (like Street Fighter V) gets the date of its first recorded price instead, which may be a pre-order date, and is marked `release_date_estimated`.

### Seasonal sale calendar

`price_history` records that a price changed, not why. [agent/data/steam_sales.json](agent/data/steam_sales.json) lists Steam's seasonal sales (Summer, Autumn and Winter from 2016 on, Spring from 2023 on), including announced future sales, and [agent/sale_calendar.py](agent/sale_calendar.py) uses it to tell seasonal discounts from a publisher's own:

- `load_sales()` reads the file, oldest first, and raises an error for a malformed entry, a sale that ends before it starts, or two overlapping sales.
- `tag_discounts(history, sales)` adds `"sale"` to each history row: the sale's name (for example `"Summer Sale 2025"`) for a discount that started in its window or up to a day before, else `None`.
- `upcoming_sales(today, sales)` returns the next sales, including one that's on now.

Each entry has a `season`, `start` and `end` (as Valve announces them; sales start and end at 10am Pacific) and `verified`. `verified` is `false` where sources conflict or none was found, with a `note` saying which. To add a sale, add an entry; the name is built from the season and the start year.

### Target-price prediction

[agent/prediction.py](agent/prediction.py) estimates when a game will cost a target price or less. `predict_target_price(target, history, target_price, comparables, comparable_histories, sales, today)` only computes from data already fetched (no API calls) and returns a `status`, a `basis`, a `confidence`, a `projected_date`, a `facts` block in Markdown (shown to the user as written, so every number is right and nothing is left out) and a one-line `headline` for the LLM's intro. The headline words the projection by confidence: "might" (low), "is likely to" (medium), "is very likely to" (high).

- **Own history**, when the game has reached the target at least twice (a price up to 3% above it counts, so a game that keeps stopping at $35.99 counts for a $35 target; the facts block then says "4 times at $35.99 (within 3% of your $35.00; never lower)" and the headline "about $35.00"): the median gap between those times projects the next one. The facts block gives the last time with what caused it (a seasonal sale, the publisher's own sale, or a regular-price cut) and how many past sales of each season reached the target.
- **Comparables**, otherwise: the target becomes a fraction of the launch price ($35 for a $69.99 game is 50%), and each comparable's age when it first cost that fraction of its own launch price gives the projected age. A fraction works across currencies and launch prices, and counts permanent price cuts as well as sales. Comparables with an estimated release date are left out unless they're all there is, since the estimate may be a pre-order date.

It also lists up to 3 similar games (an unused one, with an estimated release date, is marked), the game's regular-price cuts, or the similar games' first cuts if it has none (for "when will the regular price drop?"), flags reception that's much weaker or stronger than the comparables', and names the next seasonal sales around the projected date. A projected date that's already past is shown as "due at any sale now", so the reply never predicts a month that's gone. "Already at the target" stays strict: a current price within 3% above it gets "$0.99 above your target" instead. `status` is `already_met`, `predicted`, `not_seen` (neither the game nor its comparables have been that cheap) or `no_data`. Confidence is high with three or more of the game's own reaches, medium with two, or with three same-publisher, same-tier comparables, and low otherwise.

The older notebook [agent/steam_price_db_init.ipynb](agent/steam_price_db_init.ipynb) still creates only `game_library`. It drops the table on every run and can add a sample game.

### Running

The local Gradio chat UI, at http://127.0.0.1:7860 by default:

```bash
uv run python agent/gradio_app.py
```

## ML service API

[agent/api.py](agent/api.py) serves the agent over HTTP for the Spring Boot backend. Start it from the repo root:

```bash
uv run uvicorn api:app --app-dir agent --port 8000
```

Start-up checks the IsThereAnyDeal key, creates any missing database tables and loads the embedding model and vector store, so it takes a few seconds. Interactive docs (OpenAPI) are at http://localhost:8000/docs.

The service keeps no user state. The backend sends the whole conversation (`{"role": "user" | "assistant", "content"}` messages, oldest first) and the user's country with every chat request, and stores the replies itself.

| Endpoint                   | Purpose                                                                                                                                          |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `POST /chat`               | The reply to the user's new message, which must be the last message.                                                                             |
| `POST /chat/stream`        | The same, as server-sent events: `delta` events with `{"text"}` as the reply is written, then `done` with the `/chat` body (or `error`).         |
| `POST /chat/select`        | The reply when the user picks one of a reply's choices. Returns the `user_message` the pick stands for, to store in the conversation.            |
| `POST /chat/select/stream` | The same, as server-sent events.                                                                                                                 |
| `GET /games/search`        | Finds a game by title without the model (`query`, `country`, `search_online`). See [Search and prices](#search-and-prices).                 |
| `POST /prices`             | Current Steam prices for up to 100 games, for the price-alert job. See [Search and prices](#search-and-prices).                             |
| `GET /health`              | Liveness only: the service is up. It doesn't check Ollama or IsThereAnyDeal.                                                                |

A reply is:

```json
{
  "content": "Markdown message",
  "game": { "id": "itad-game-id", "title": "Hades" },
  "choices": {
    "source": "catalog",
    "query": "prey",
    "games": [{ "id": "...", "title": "Prey" }]
  }
}
```

- `game` is the game the reply is about (a price or a prediction), so the frontend can offer to track it. Otherwise `null`.
- `choices` lists games to show as buttons under the message. Otherwise `null`. Send the pick to `/chat/select` as `{"type": "game", "id", "title"}`. With source `catalog`, also offer "None of these, search online" (`{"type": "search_online", "query"}`), and with `rag`, "None of these" (`{"type": "none_of_these"}`).
- Predictions and choices arrive as a single `delta`, since they're checked or built as a whole. Price summaries stream token by token.
- A model that can't be reached gets a `503`. Bad requests (an unknown country, a conversation that doesn't end with a user message) get a `422`.

### Search and prices

These two endpoints use IsThereAnyDeal and the local cache, not the model, so they keep working when Ollama is down. Their response schemas are in the OpenAPI document at `/openapi.json`, for generating the backend's client. Both results are tagged by `status`.

| `status`         | From              | Meaning                                                                                                     |
| ---------------- | ----------------- | ----------------------------------------------------------------------------------------------------------- |
| `match`          | search and prices | The game's `id`, `title`, `current_deal`, `historical_lowest` (`null` for some games) and `cached`.         |
| `no_price`       | search and prices | The game exists but has no Steam price. Asking again won't help.                                            |
| `choices`        | search            | Several games match. With `source` `catalog`, also offer to search again with `search_online=true`.         |
| `not_found`      | search            | No game matches the query.                                                                                  |
| `not_in_catalog` | prices            | The id was never returned by a search, so the service doesn't know the game.                                |
| `error`          | prices            | The lookup failed for this game. Worth trying again later.                                                  |

- If IsThereAnyDeal fails during a search, `/games/search` returns a `502`.
- `/prices` always returns one result per id, in request order. Failures are per game, so an outage still returns the prices newer than `max_age_hours`. An older one comes back as `error`, not as a stale price.
- `/prices` makes at most one IsThereAnyDeal request, however many games are in it. Only games without a fresh cached price are fetched.
- Prices are cached for 24 hours per game and country, so by default a discount can show up to 24 hours late. `/prices` takes an optional `max_age_hours` (0 to 24, default 24) for how old a cached price can be: the alert job can pass `6` to see discounts within 6 hours, or `0` to fetch every price. A fetched price also refreshes the cache that chat and search read.

## Tests

```bash
uv run pytest
```

The tests cover `slugify`, the HTTP retry/backoff logic (with the network and `time.sleep` mocked out), knowledge-base chunking (including game header metadata), the company matching and ranking behind `find_game_candidates`, the database init (including adding `steam_appid` to an existing `game_catalog`), the price history and game info caches, the comparable-game ranking (with the API mocked out), the seasonal sale calendar, the target-price prediction, the agent's turns (with the model and APIs faked) and the HTTP API's requests, responses and streaming. They need none of the ML stack, so [GitHub Actions](.github/workflows/tests.yml) runs them on every push and pull request with only the `dev` dependency group (`uv run --only-group dev pytest`).

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
├── tracker.py                  price tools and the Llama 3.1 agent, one stateless turn at a time
├── api.py                      FastAPI service over the agent, for the Spring Boot backend
├── gradio_app.py               local Gradio chat UI over the agent
├── steam_price_db_init.py      creates the SQLite price cache, game catalog and price history tables
├── price_history.py            Steam price history from IsThereAnyDeal, cached in SQLite
├── game_info.py                game details (release date, companies, reviews, players) from IsThereAnyDeal, cached in SQLite
├── comparables.py              finds comparable games for price predictions (same publisher, price tier, reception)
├── sale_calendar.py            Steam's seasonal sale windows, for tagging discounts as seasonal sales
├── prediction.py               when a game will reach a target price, from its own or its comparables' history
├── data/steam_sales.json       seasonal sale dates since 2016
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
