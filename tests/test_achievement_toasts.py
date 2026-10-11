"""Unlocking many achievements at once shows two toasts and a "+N more" link,
not a column that fills the phone screen."""
import pytest

from app.routes.achievements import ACHIEVEMENTS


@pytest.mark.asyncio
async def test_unseen_shows_two_toasts_then_a_more_link(client, db_conn):
    ids = [a["id"] for a in ACHIEVEMENTS[:8]]
    for aid in ids:
        await db_conn.execute(
            "INSERT INTO user_achievements(user_id, achievement_id, seen) VALUES (1, ?, 0)", (aid,))
    await db_conn.commit()
    html = (await client.get("/achievements/unseen")).text
    assert html.count('class="ach-toast"') == 2
    assert "+6 more achievements unlocked" in html and 'href="/achievements"' in html
    assert (await client.get("/achievements/unseen")).text == ""   # all marked seen


@pytest.mark.asyncio
async def test_two_unseen_need_no_more_link(client, db_conn):
    for aid in [a["id"] for a in ACHIEVEMENTS[:2]]:
        await db_conn.execute(
            "INSERT INTO user_achievements(user_id, achievement_id, seen) VALUES (1, ?, 0)", (aid,))
    await db_conn.commit()
    html = (await client.get("/achievements/unseen")).text
    assert html.count('class="ach-toast"') == 2 and "more achievement" not in html
