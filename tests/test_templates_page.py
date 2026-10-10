"""GET /templates: each template lists its own exercises, in order, for its owner only."""
import pytest


async def _ex(db, name):
    async with db.execute("INSERT INTO exercises(name) VALUES (?)", (name,)) as cur:
        return cur.lastrowid


async def _template(db, user_id, name, exercise_ids):
    async with db.execute(
        "INSERT INTO workout_templates(user_id, name) VALUES (?, ?)", (user_id, name)
    ) as cur:
        tid = cur.lastrowid
    for i, ex in enumerate(exercise_ids):
        await db.execute(
            "INSERT INTO workout_template_exercises(template_id, exercise_id, order_idx) VALUES (?, ?, ?)",
            (tid, ex, i),
        )
    return tid


@pytest.mark.asyncio
async def test_templates_page_groups_exercises_per_template_in_order(client, db, user_b):
    a, b, c = await _ex(db, "Tpl Alpha"), await _ex(db, "Tpl Bravo"), await _ex(db, "Tpl Charlie")
    await _template(db, 1, "Push A", [c, a])          # order_idx decides the order, not the id
    await _template(db, 1, "Empty One", [])
    await _template(db, 2, "Someone Else", [b])
    await db.commit()

    html = (await client.get("/templates")).text

    push = html[html.index("Push A"):]
    push = push[:push.index("</div>", push.index("Tpl Alpha"))]
    assert push.index("Tpl Charlie") < push.index("Tpl Alpha")
    assert "Tpl Bravo" not in html and "Someone Else" not in html
    empty = html[html.index("Empty One"):]
    assert "No exercises" in empty[:600]
