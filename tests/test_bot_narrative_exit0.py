"""בוט narrative-exit0 — תופס דיספוץ' שיצא קוד 0 אבל ענה בנרטיב.

הבדיקה הדטרמיניסטית רצה לפני המודל (docs/bots/CONTRACT.md). הטסטים
כאן מדלגים על ollama (`INBOX_SKIP_MODEL=1`) ומזריקים תמלילי runner
מזויפים, כדי שהשאלה תהיה על הספירה ולא על ניסוח. בוט שתמיד מדווח
חסר ערך בדיוק כמו בוט ששותק תמיד — לכן כאן שני הצדדים: מדווח על
נרטיב+קוד 0+אין PR, ושותק כשנפתח PR, כשהקוד אינו 0, וכשאין דיספוץ'.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "tools/agents/bot-narrative-exit0.sh"
BASH = shutil.which("bash")
requires_bash = pytest.mark.skipif(
    BASH is None and os.name == "nt",
    reason="אין bash בווינדוס הזה (Git Bash לא מותקן) — רץ ב-CI ועל לינוקס",
)

NARRATIVE = (
    "I'll start by analyzing the issue and then I will implement the fix "
    "step by step, making sure everything is consistent with the rest of "
    "the project before I open a pull request for review and validation.\n"
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def seed(tmp: Path, agent: str, num: int, code: int, pr: str,
         output: str, *, write_output: bool = True) -> Path:
    """תמליל runner מזויף + קובץ פלט הסוכן, כפי ש-agent-runner.ps1 כותב."""
    log_dir = tmp / "runner-logs"
    pr_line = "STATUS: pr-open" if pr == "open" else (
        "STATUS: no-pr" if pr == "none" else "")
    lines = [
        "**********************",
        "Windows PowerShell transcript start",
        "STATUS: runner-on",
        f"STATUS: task #{num} some title here",
        "STATUS: worktree abcdef1",
        f"STATUS: {agent}-exit {code}",
    ]
    if pr_line:
        lines.append(pr_line)
    lines += ["**********************", "Transcript stopped"]
    _write(log_dir / f"run-20260910-00000{num}.log", "\n".join(lines) + "\n")
    if write_output:
        _write(log_dir / f"{agent}-{num}.log", output)
    return log_dir


def run_bot(tmp: Path):
    log_dir = tmp / "runner-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    inbox = tmp / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "INBOX_SKIP_MODEL": "1",
        "INBOX_DIR": inbox.as_posix(),
        "INBOX_STAMP": "2026-09-10-120000",
        "NARRATIVE_LOG_DIR": log_dir.as_posix(),
    })
    proc = subprocess.run(
        [BASH, str(BOT)],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, env=env, timeout=30,
    )
    out = inbox / "2026-09-10-120000-narrative-exit0.md"
    return proc, out, inbox


def test_is_a_script_with_a_file_prompt():
    prompt = BOT.with_suffix(".md")
    assert BOT.is_file(), BOT
    assert prompt.is_file(), prompt
    src = BOT.read_text(encoding="utf-8")
    assert src.splitlines()[0] == "#!/usr/bin/env bash"
    assert 'ollama run "$MODEL" < "$prompt"' in src
    assert "INBOX_MODEL" in src
    assert "qwen2.5-coder:32b" in src


@requires_bash
def test_reports_narrative_opener_with_exit0_and_no_pr(tmp_path):
    seed(tmp_path, "grok", 614, 0, "none", NARRATIVE)
    proc, out, _ = run_bot(tmp_path)
    assert proc.returncode == 0, proc.stderr
    text = out.read_text(encoding="utf-8")
    assert "## candidate 1" in text
    assert "exit=0 pr=no" in text
    assert "## nothing else found" in text


@requires_bash
def test_reports_short_output_with_exit0_and_no_pr(tmp_path):
    seed(tmp_path, "grok", 42, 0, "none", "done.\n")
    proc, out, _ = run_bot(tmp_path)
    assert proc.returncode == 0, proc.stderr
    text = out.read_text(encoding="utf-8")
    assert "## candidate 1" in text
    assert "size=6" in text  # "done.\n" = 6 bytes
    assert "## nothing else found" in text


@requires_bash
def test_silent_when_a_pr_was_opened(tmp_path):
    seed(tmp_path, "grok", 7, 0, "open", NARRATIVE)
    proc, out, _ = run_bot(tmp_path)
    assert proc.returncode == 0, proc.stderr
    text = out.read_text(encoding="utf-8")
    assert "## candidate" not in text
    assert "## nothing else found" in text


@requires_bash
def test_silent_when_exit_code_is_nonzero(tmp_path):
    seed(tmp_path, "grok", 8, 1, "none", NARRATIVE)
    proc, out, _ = run_bot(tmp_path)
    assert proc.returncode == 0, proc.stderr
    text = out.read_text(encoding="utf-8")
    assert "## candidate" not in text
    assert "## nothing else found" in text


@requires_bash
def test_silent_when_output_is_substantive(tmp_path):
    body = ("Opened the router, read the handler, and traced the bug to a "
            "missing guard. See server/units.py:210 for the offending line. "
            "The fix adds the check and a regression test, then runs the "
            "suite to confirm the previously failing case now passes and no "
            "other case regressed. Details of the change and the reasoning "
            "follow below for the reviewer to weigh before any merge.\n")
    assert len(body.encode()) > 200
    seed(tmp_path, "grok", 9, 0, "none", body)
    proc, out, _ = run_bot(tmp_path)
    assert proc.returncode == 0, proc.stderr
    text = out.read_text(encoding="utf-8")
    assert "## candidate" not in text
    assert "## nothing else found" in text


@requires_bash
def test_could_not_check_when_output_log_missing(tmp_path):
    seed(tmp_path, "grok", 10, 0, "none", "", write_output=False)
    proc, out, _ = run_bot(tmp_path)
    assert proc.returncode == 0, proc.stderr
    text = out.read_text(encoding="utf-8")
    assert "could not check" in text
    assert "## candidate" not in text
    assert "## nothing else found" in text


@requires_bash
def test_could_not_check_when_pr_status_unknown(tmp_path):
    seed(tmp_path, "grok", 11, 0, "", NARRATIVE)  # no pr-open/no-pr line
    proc, out, _ = run_bot(tmp_path)
    assert proc.returncode == 0, proc.stderr
    text = out.read_text(encoding="utf-8")
    assert "could not check" in text
    assert "## candidate" not in text


@requires_bash
def test_a_clean_run_still_writes_the_inbox_file(tmp_path):
    """שתיקה אינה היעדר קובץ — חוזה תנאי 2. אין דיספוצ'ים = קובץ עם
    '## nothing else found' בלבד."""
    proc, out, _ = run_bot(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "## candidate" not in text
    assert "## nothing else found" in text
