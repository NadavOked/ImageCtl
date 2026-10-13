"""#1322 D: what a cloning machine reports after a local disk-to-disk clone.

The copy itself never passes through the server -- the machine copies one of
its drawers to the others and works with the server down. When the server
answers, the agent (``agent/lib/localclone.sh`` ``lc_report``) sends what it
did, and it becomes one journal line, labelled as the machine's own report.
Best effort from the agent's side: a refused report changes nothing on the
machine's screen. Nothing here is trusted beyond a line of text -- it is not
a round, not a disk failure record, and it moves no state.
"""

from __future__ import annotations

from typing import Any

#: one error text from the machine, at most -- the journal is read by people
ERROR_MAX = 200


class BadReport(ValueError):
    """The body is not a local clone report."""


def _drawer(item: Any) -> str:
    if not isinstance(item, dict):
        raise BadReport("a drawer is not an object")
    port = item.get("port")
    dev = item.get("dev")
    if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 100:
        return f"דיסק {port}"
    if isinstance(dev, str) and dev.isalnum() and len(dev) <= 16:
        return dev
    raise BadReport("a drawer has neither a port nor a device name")


def _amount(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return f"{value / 1e9:.1f} GB"
    return "כמות לא נמדדה"


def journal_line(body: dict) -> str:
    """The journal's detail for one report, or BadReport."""
    source = _drawer(body.get("source"))
    targets = body.get("targets")
    if not isinstance(targets, list) or not targets or len(targets) > 8:
        raise BadReport("targets must be a list of 1-8 drawers")
    parts = []
    for item in targets:
        name = _drawer(item)
        state = item.get("state")
        if state == "done":
            parts.append(f"{name} הצליח")
        elif state == "failed":
            error = item.get("error") if isinstance(item.get("error"), str) else ""
            error = " ".join(error.split())[:ERROR_MAX]
            parts.append(f"{name} נכשל" + (f" ({error})" if error else ""))
        else:
            raise BadReport(f"unknown target state: {state!r}")
    elapsed = body.get("elapsed_s")
    when = (f"{elapsed // 60}:{elapsed % 60:02d}"
            if isinstance(elapsed, int) and not isinstance(elapsed, bool) and elapsed >= 0 else "זמן לא נמדד")
    line = f"מקור {source} → " + ", ".join(parts) + f" · {_amount(body.get('bytes'))} · {when}"
    if body.get("stopped") is True:
        line += " · נעצר באמצע"
    if body.get("shrunk") is True:
        restore = body.get("source_restore")
        line += " · המקור כווץ זמנית " + ("והוחזר לגודלו" if restore == "ok"
                                          else "ולא הוחזר לגודלו — לבדוק את המקור")
    return line + " (דיווח המחשב — השיכפול לא עבר דרך השרת)"
