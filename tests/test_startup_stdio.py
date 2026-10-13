from __future__ import annotations

import io

import server.main


def test_first_login_message_is_printable_on_cp1252(monkeypatch) -> None:
    output = io.BytesIO()
    stdout = io.TextIOWrapper(output, encoding="cp1252")
    monkeypatch.setattr(server.main.sys, "stdout", stdout)

    server.main._configure_stdio()
    print(
        "\n  \u05db\u05e0\u05d9\u05e1\u05d4 \u05e8\u05d0\u05e9\u05d5\u05e0\u05d4: admin / admin \u2014 "
        "\u05d4\u05e7\u05d5\u05e0\u05e1\u05d5\u05dc\u05d4 \u05ea\u05d3\u05e8\u05d5\u05e9 \u05d4\u05d7\u05dc\u05e4\u05ea \u05e1\u05d9\u05e1\u05de\u05d4\n",
        flush=True,
    )

    assert stdout.errors == "backslashreplace"
    assert b"\\u05db\\u05e0\\u05d9\\u05e1\\u05d4" in output.getvalue()
    assert b"admin / admin" in output.getvalue()
