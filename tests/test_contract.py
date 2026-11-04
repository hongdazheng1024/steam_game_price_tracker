import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import TypeAdapter

import api

CONTRACT_DIR = Path(__file__).parent.parent / "contract" / "ml-service"
SEARCH_RESULT = TypeAdapter(api.SearchResult)


def fixture(name):
    return json.loads((CONTRACT_DIR / f"{name}.json").read_text(encoding="utf-8"))


def statuses(union):
    # Annotated[A | B, Field(...)] -> the status of each of A and B
    return {get_args(model.model_fields["status"].annotation)[0] for model in get_args(get_args(union)[0])}


@pytest.mark.parametrize("name", ["search-match", "search-no-price", "search-choices", "search-not-found"])
def test_search_fixtures_are_what_the_endpoint_sends(name):
    sample = fixture(name)
    assert SEARCH_RESULT.dump_python(SEARCH_RESULT.validate_python(sample), mode="json") == sample


def test_prices_fixture_is_what_the_endpoint_sends():
    sample = fixture("prices")
    assert api.PricesResponse.model_validate(sample).model_dump(mode="json") == sample


def test_fixtures_cover_every_status():
    # A new status must get a fixture, so the backend's tests have to handle it
    searched = {fixture(name)["status"] for name in ["search-match", "search-no-price", "search-choices", "search-not-found"]}
    assert searched == statuses(api.SearchResult)
    assert {price["status"] for price in fixture("prices")["prices"]} == statuses(api.PriceResult)
