"""HTTP API over the price-tracking agent, for the Spring Boot backend.

The service keeps no user state: the backend sends the whole conversation with every chat request and
stores the replies itself. Its only storage is the SQLite price cache (steam_tracker.db).

Run from the repo root:
    uv run uvicorn api:app --app-dir agent --port 8000
The interactive docs are then at http://localhost:8000/docs."""
import itertools
import json
import logging
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

load_dotenv()

import steam_price_db_init  # noqa: E402 (after load_dotenv, so tracker sees the .env settings)
import tracker  # noqa: E402

log = logging.getLogger(__name__)

Country = Literal["US", "JP", "GB", "CA", "AU", "DE", "FR"]
assert list(Country.__args__) == tracker.COUNTRIES
# Enough for the alert job to check a batch of tracked games per request
MAX_PRICE_IDS = 100


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    tracker.itad_api_key()  # fail at start-up, not on the first request
    steam_price_db_init.init_db()
    # Loads the embedding model and the vector store now, so the first description search isn't slow
    tracker.knowledge_base()
    yield


app = FastAPI(title="Steam Price Tracker ML service", lifespan=lifespan)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, description="The whole conversation, oldest first, "
                                                              "ending with the user's new message.")
    country: Country = "US"

    @field_validator("messages")
    @classmethod
    def ends_with_user_message(cls, messages: list[Message]) -> list[Message]:
        if messages[-1].role != "user" or not messages[-1].content.strip():
            raise ValueError("the last message must be a non-empty user message")
        return messages


class GameChoice(BaseModel):
    type: Literal["game"]
    id: str
    title: str


class SearchOnlineChoice(BaseModel):
    type: Literal["search_online"]
    query: str


class NoneOfTheseChoice(BaseModel):
    type: Literal["none_of_these"]


Choice = Annotated[GameChoice | SearchOnlineChoice | NoneOfTheseChoice, Field(discriminator="type")]


class SelectRequest(BaseModel):
    messages: list[Message] = Field(description="The conversation so far, ending with the reply whose choice "
                                                "the user picked.")
    choice: Choice
    country: Country = "US"


class Game(BaseModel):
    id: str
    title: str


class Choices(BaseModel):
    source: Literal["catalog", "api", "rag"] = Field(
        description='"catalog": also offer "None of these, search online" (a search_online choice). '
                    '"rag": also offer "None of these" (a none_of_these choice).')
    query: str
    games: list[Game]


class Reply(BaseModel):
    content: str = Field(description="The assistant's message, in Markdown.")
    game: Game | None = Field(description="The game the reply is about (a price or a prediction), to offer "
                                          "tracking it.")
    choices: Choices | None = Field(description="Games for the user to pick from, as buttons under the message.")


class ChatResponse(BaseModel):
    reply: Reply


class SelectResponse(BaseModel):
    user_message: str = Field(description="The user message the pick stands for, to store in the conversation.")
    reply: Reply


class PricesRequest(BaseModel):
    game_ids: list[str] = Field(min_length=1, max_length=MAX_PRICE_IDS)
    country: Country = "US"
    max_age_hours: float = Field(default=24, ge=0, le=24,
                                 description="How old a cached price can be. Older prices are fetched again. "
                                             "0 fetches every price.")


class Money(BaseModel):
    amount: float
    amountInt: int = Field(description="The amount in the currency's smallest unit, such as cents.")
    currency: str


class Shop(BaseModel):
    id: int
    name: str


class Deal(BaseModel):
    shop: Shop
    price: Money
    regular: Money
    cut: int = Field(description="The discount, in percent.")
    timestamp: str | None = None
    expiry: str | None = Field(default=None, description="When the deal ends, if known. Only on a current deal.")
    url: str | None = Field(default=None, description="Where to buy. Only on a current deal.")


class PriceMatch(BaseModel):
    status: Literal["match"]
    id: str
    title: str
    current_deal: Deal | None
    historical_lowest: Deal | None = Field(description="The all-time low on Steam. IsThereAnyDeal has none for "
                                                       "some games.")
    cached: bool


class NoPrice(BaseModel):
    """The game exists but has no Steam price, so asking again won't help."""
    status: Literal["no_price"]
    id: str
    title: str


class SearchChoices(BaseModel):
    """Several games match. With source "catalog" the list only covers games stored so far, so also offer to
    search again with search_online=true."""
    status: Literal["choices"]
    choices: list[Game]
    source: Literal["catalog", "api"]
    query: str


class SearchNotFound(BaseModel):
    status: Literal["not_found"]
    query: str


SearchResult = Annotated[PriceMatch | NoPrice | SearchChoices | SearchNotFound, Field(discriminator="status")]


class NotInCatalog(BaseModel):
    """The game was never found through /games/search, so this service doesn't know it."""
    status: Literal["not_in_catalog"]
    id: str


class PriceError(BaseModel):
    """The lookup failed, so it's worth trying this game again later."""
    status: Literal["error"]
    id: str
    title: str
    error: str


PriceResult = Annotated[PriceMatch | NoPrice | NotInCatalog | PriceError, Field(discriminator="status")]


class PricesResponse(BaseModel):
    prices: list[PriceResult] = Field(description="One result per requested id, in the same order.")


class Health(BaseModel):
    status: Literal["ok"]


@app.exception_handler(tracker.LLMUnavailable)
async def llm_unavailable(request, exc: tracker.LLMUnavailable):
    log.warning("%s", exc)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(tracker.MissingConfig)
async def missing_config(request, exc: tracker.MissingConfig):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


def conversation(messages: list[Message]) -> list[dict]:
    return [message.model_dump() for message in messages]


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def event_stream(events: Iterator[dict], done: Callable[[dict], dict]) -> Iterator[str]:
    # Server-sent events: "delta" with {"text"} as the reply is written, then "done" with the same body as
    # the non-streaming endpoint, or "error" with {"detail"} if the turn fails after the stream started
    try:
        for event in events:
            if event["type"] == "delta":
                yield sse("delta", {"text": event["text"]})
            else:
                yield sse("done", done(event["reply"]))
    except (tracker.LLMUnavailable, tracker.MissingConfig) as e:
        log.warning("%s", e)
        yield sse("error", {"detail": str(e)})


def streaming(events: Iterator[dict], done: Callable[[dict], dict]) -> StreamingResponse:
    # The first event is read before responding, so a model that's down still gets a 503, not a broken stream
    first = next(events)
    return StreamingResponse(event_stream(itertools.chain([first], events), done), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


# Plain `def` endpoints: the agent blocks on the model and the APIs, so FastAPI runs them in its thread pool

@app.get("/health", response_model=Health)
def health() -> dict:
    """Liveness only: the service is up. It doesn't check Ollama or IsThereAnyDeal."""
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> dict:
    """The assistant's reply to the user's new message."""
    return {"reply": tracker.final_reply(tracker.chat_turn(conversation(request.messages), request.country))}


@app.post("/chat/stream", response_class=StreamingResponse,
          responses={200: {"content": {"text/event-stream": {}},
                           "description": 'Server-sent events: "delta" {"text"}, then "done" (a ChatResponse) '
                                          'or "error" {"detail"}.'}})
def chat_stream(request: ChatRequest):
    """/chat, streamed as the reply is written."""
    events = tracker.chat_turn(conversation(request.messages), request.country)
    return streaming(events, lambda reply: {"reply": reply})


@app.post("/chat/select", response_model=SelectResponse)
def select(request: SelectRequest) -> dict:
    """The assistant's reply when the user picks one of a reply's choices."""
    choice = request.choice.model_dump()
    reply = tracker.final_reply(tracker.select_option(conversation(request.messages), choice, request.country))
    return {"user_message": tracker.selection_message(choice), "reply": reply}


@app.post("/chat/select/stream", response_class=StreamingResponse,
          responses={200: {"content": {"text/event-stream": {}},
                           "description": 'Server-sent events: "delta" {"text"}, then "done" (a SelectResponse) '
                                          'or "error" {"detail"}.'}})
def select_stream(request: SelectRequest):
    """/chat/select, streamed as the reply is written."""
    choice = request.choice.model_dump()
    events = tracker.select_option(conversation(request.messages), choice, request.country)
    user_message = tracker.selection_message(choice)
    return streaming(events, lambda reply: {"user_message": user_message, "reply": reply})


@app.get("/games/search", response_model=SearchResult, responses={502: {"description": "IsThereAnyDeal failed."}})
def search_games(query: Annotated[str, Query(min_length=1)], country: Country = "US",
                 search_online: bool = False) -> dict:
    """Finds a game by title, without the model."""
    result = json.loads(tracker.search_game(query, country, search_online))
    if result["status"] == "error":
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@app.post("/prices", response_model=PricesResponse)
def prices(request: PricesRequest) -> dict:
    """Current Steam prices for games already found through /games/search, for the price-alert job. Failures are
    per game, so an IsThereAnyDeal outage still returns the prices newer than max_age_hours."""
    max_age = timedelta(hours=request.max_age_hours)
    return {"prices": tracker.current_prices(request.game_ids, request.country, max_age)}
