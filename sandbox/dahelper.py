"""Sandbox-side helper baked into the sandbox image.

The LLM-generated analysis script imports this as `dahelper` and uses it to
report results back to the host in a stable JSON contract (result.json),
instead of printing free-form output that the host would have to parse.
"""

import json
import pathlib
import re

OUT_DIR = pathlib.Path("/out")
RESULT_PATH = OUT_DIR / "result.json"
MAX_TEXT = 4000
MAX_TABLE_ROWS = 100


def _load() -> dict:
    if RESULT_PATH.exists():
        try:
            return json.loads(RESULT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"text": "", "tables": {}, "charts": [], "stdout_note": ""}


def _save(state: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _safe_name(name: str) -> str:
    name = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", name).strip("_")
    return name or "table"


def save_text(text: str) -> None:
    """Save the main natural-language answer (call once at the end)."""
    state = _load()
    state["text"] = str(text)[:MAX_TEXT]
    _save(state)


def save_table(name: str, rows) -> None:
    """Save a named result table. `rows` is a list of dicts (e.g. df.to_dict('records'))."""
    state = _load()
    rows = [dict(r) for r in list(rows)[:MAX_TABLE_ROWS]]
    state["tables"][_safe_name(name)] = rows
    _save(state)


def save_chart(fig=None, name: str = "chart") -> str:
    """Save a matplotlib figure (defaults to the current one) as PNG in /out.

    Returns the filename so the script can mention it if needed.
    """
    import matplotlib.pyplot as plt

    state = _load()
    if fig is None:
        fig = plt.gcf()
    fname = f"{_safe_name(name)}.png"
    fig.savefig(OUT_DIR / fname, dpi=120, bbox_inches="tight")
    if fname not in state["charts"]:
        state["charts"].append(fname)
    _save(state)
    return fname
