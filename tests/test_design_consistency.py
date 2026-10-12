"""Regression tests from the 2026-10-12 design consistency pass."""
import pytest

from tests.test_bodyweight_records import _pullup_id

HTML = {"Accept": "text/html"}


@pytest.mark.asyncio
async def test_a_new_bodyweight_exercise_offers_no_weight_goal(client):
    # Before any set was logged, the page didn't know Pull-up was bodyweight and
    # offered "Set a weight goal for this exercise".
    ex = await _pullup_id(client)
    page = (await client.get(f"/exercises/{ex}", headers=HTML)).text
    assert "No sets logged for this exercise" in page
    assert "goal-set-trigger" not in page
