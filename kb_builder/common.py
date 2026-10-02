import random
import re
import time
from pathlib import Path

import httpx

USER_AGENT = "steam-game-price-tracker/0.1 (personal research project);"

REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_BASE_DIR = REPO_ROOT / "knowledge-base"
TEST_KNOWLEDGE_BASE_DIR = REPO_ROOT / "knowledge-base-test"


# Minimum seconds between requests to a host (a little random jitter is added on top).
MIN_REQUEST_INTERVAL = {"en.wikipedia.org": 1.0}
JITTER_SECONDS = 0.5
RETRY_STATUSES = {429, 502, 503, 504}
MAX_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 60

_last_request_at: dict[str, float] = {}


def _throttle(host: str) -> None:
    interval = MIN_REQUEST_INTERVAL.get(host, 0)
    if interval:
        wait = _last_request_at.get(
            host, 0) + interval + random.uniform(0, JITTER_SECONDS) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
    _last_request_at[host] = time.monotonic()


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After", "")
    delay = float(retry_after) if retry_after.isdigit() else 2**attempt
    return min(delay, MAX_BACKOFF_SECONDS) + random.uniform(0, JITTER_SECONDS)


class ThrottledRetryTransport(httpx.BaseTransport):
    """Spaces out requests per host and retries rate-limit/server errors, honoring Retry-After."""

    def __init__(self):
        self._transport = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            _throttle(request.url.host)
            response = self._transport.handle_request(request)
            if response.status_code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                return response
            delay = _retry_delay(response, attempt)
            response.close()
            print(
                f"    [retry] {response.status_code} from {request.url.host}, waiting {delay:.1f}s")
            time.sleep(delay)

    def close(self) -> None:
        self._transport.close()


def http_client(**kwargs) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=15, transport=ThrottledRetryTransport(), **kwargs
    )


def slugify(name: str) -> str:
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def write_markdown(directory: Path, name: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slugify(name)}.md"
    path.write_text(content, encoding="utf-8")
    return path
