import httpx
import pytest

from kb_builder import common
from kb_builder.common import MAX_ATTEMPTS, MAX_BACKOFF_SECONDS, ThrottledRetryTransport, _retry_delay


@pytest.fixture(autouse=True)
def no_jitter_or_sleep(monkeypatch):
    """Make delays deterministic and keep tests from actually sleeping."""
    monkeypatch.setattr(common.random, "uniform", lambda a, b: 0.0)
    sleeps = []
    monkeypatch.setattr(common.time, "sleep", sleeps.append)
    common._last_request_at.clear()
    return sleeps


def response(headers=None):
    return httpx.Response(429, headers=headers)


@pytest.mark.parametrize("attempt, expected", [(1, 2), (2, 4), (3, 8), (5, 32)])
def test_exponential_backoff_without_retry_after(attempt, expected):
    assert _retry_delay(response(), attempt) == expected


def test_backoff_is_capped():
    assert _retry_delay(response(), 10) == MAX_BACKOFF_SECONDS


def test_retry_after_header_is_honored():
    assert _retry_delay(response({"Retry-After": "7"}), 1) == 7


def test_retry_after_is_capped():
    assert _retry_delay(response({"Retry-After": "3600"}), 1) == MAX_BACKOFF_SECONDS


def test_non_numeric_retry_after_falls_back_to_backoff():
    # HTTP-date form of Retry-After isn't parsed; exponential backoff is used instead.
    header = {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}
    assert _retry_delay(response(header), 3) == 8


def test_jitter_is_added(monkeypatch):
    monkeypatch.setattr(common.random, "uniform", lambda a, b: b)
    assert _retry_delay(response(), 1) == 2 + common.JITTER_SECONDS


def make_transport(statuses, monkeypatch):
    """A ThrottledRetryTransport whose inner transport replies with the given statuses in order."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(statuses[len(calls) - 1])

    transport = ThrottledRetryTransport()
    monkeypatch.setattr(transport, "_transport", httpx.MockTransport(handler))
    return transport, calls


def send(transport):
    return transport.handle_request(httpx.Request("GET", "https://store.steampowered.com/api"))


def test_transport_retries_until_success(no_jitter_or_sleep, monkeypatch):
    transport, calls = make_transport([503, 429, 200], monkeypatch)
    assert send(transport).status_code == 200
    assert len(calls) == 3
    assert no_jitter_or_sleep == [2, 4]


def test_transport_does_not_retry_client_errors(monkeypatch):
    transport, calls = make_transport([404], monkeypatch)
    assert send(transport).status_code == 404
    assert len(calls) == 1


def test_transport_gives_up_after_max_attempts(monkeypatch):
    transport, calls = make_transport([503] * MAX_ATTEMPTS, monkeypatch)
    assert send(transport).status_code == 503
    assert len(calls) == MAX_ATTEMPTS
