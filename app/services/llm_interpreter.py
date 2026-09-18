import json

from app.services.groq_client import get_groq_client, get_groq_model

SYSTEM_PROMPT = """
You interpret GridWise operator notes into structured directives.

Allowed directive_type values:
- solar_reduction
- minimum_battery_reserve
- no_charge_window
- no_discharge_window
- max_grid_window
- no_op

Return JSON only. Return exactly one item for every input note, in note_index order.
For no_op: applies=false and structured_adjustment=null.
For all other directives: applies=true and structured_adjustment must match the challenge schema.
Hours are integers 0..23, unique, ascending, start-inclusive and end-exclusive.
For solar_reduction, factor is the usable fraction remaining (80% reduction => factor 0.2).
Do not invent demand, solar, tariff, battery parameters, or unsupported directive types.
""".strip()


def interpret_operator_notes(notes: list[str]) -> list[dict]:
    """Ask Groq to interpret operator notes.

    Deterministic validation should be added before these directives reach the optimizer.
    """
    client = get_groq_client()
    payload = {
        "operator_notes": [
            {"note_index": i, "text": note} for i, note in enumerate(notes)
        ]
    }

    response = client.chat.completions.create(
        model=get_groq_model(),
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
    )

    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("Groq returned an empty response")

    data = json.loads(content)
    return data["directive_interpretation"]
