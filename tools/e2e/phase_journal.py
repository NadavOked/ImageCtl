"""שלב 10 — היומן: כל מה שקרה הושאר אחריו עקבות, בעברית ובשמות."""

from __future__ import annotations

from .harness import check


def run(ctx) -> None:
    print("\n10. יומן")
    status, rows = ctx.console.json("GET", "/api/console/journal",)
    events = {r["event"] for r in rows}
    for needed in ("capture_start", "capture_done", "image_upload",
                   "session_open", "session_start_auto", "session_close",
                   "client_failed", "unknown_mac", "agent_login_failed",
                   "room_open", "room_wave", "room_done"):
        check(f"היומן מכיל {needed}", needed in events)
    failure = next(r for r in rows if r["event"] == "client_failed")
    check("היומן בעברית ובשמות",
          failure["label"] == "כתיבה נכשלה במחשב" and "LAB1" in failure["text"],
          f'{failure["label"]} | {failure["text"]}')
