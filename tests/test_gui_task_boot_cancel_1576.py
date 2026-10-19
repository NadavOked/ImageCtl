"""#1576 (Nadav 17/10) -- a task the build machine's GUI ordered does not
outlive a reboot that came before it started.

Metal 17/10: tsk_d58c was ordered from the GUI at 08:56 and never taken
(#1575); the machine was rebooted at 09:02 and the capture started on its own,
with nobody logged in. The request for the boot menu IS the boot
(hello.make_resolver, #75), so that is where a GUI order that is still
``pending`` is cancelled -- with its reason, and before the answer is built.
A console order is not touched: an operator who orders a capture from a
machine that is off expects exactly that boot to bring it.

**The server decides the origin by the channel, not by the body** (security
review): an order that came in through the kiosk router on the agent port is
``build_gui`` whatever its body says, and one on the console app is
``console`` even when its body claims otherwise. So these tests run on the
production split -- ``create_agent_app`` and ``create_console_app`` over one
runtime (``kiosk_env``) -- and not on the combined test app, where the kiosk
routes shadow the console's.

Negative control: without boot_cancel.py, the hook in hello.py and the mark
in kiosk.py/app.py, the GUI tasks stay ``pending`` and the first hello after
the boot hands them out.
"""

from __future__ import annotations

from conftest import hello_body
from fastapi.testclient import TestClient
from test_kiosk_app import _login_kiosk, kiosk_env  # noqa: F401
from test_server_room import cloner_hello

#: the reason as the console shows it (a literal: the negative control runs without boot_cancel.py)
REASON = "בוטלה: המכונה אותחלה לפני תחילת המשימה"

BUILD = "aa:bb:cc:00:00:10"
CLONER = "aa:bb:cc:00:00:20"     # registered in grp_CLONERS by kiosk_env


def _env(kiosk_env):
    console = TestClient(kiosk_env["console_app"])
    assert console.post("/api/console/login",
                        json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    assert console.post("/api/console/machines", json={
        "mac": BUILD, "name": "build-01", "group_id": "grp_BUILD"}).status_code == 200
    body = hello_body(BUILD)
    body["disks"] = [{"dev": "sda", "size_bytes": 256060514304, "model": "Build SSD",
                      "serial": "B-sda", "removable": False, "scheme": "gpt", "has_data": True}]
    assert kiosk_env["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
    return console, _login_kiosk(kiosk_env)


def _capture(client, body_extra=None):
    r = client.post("/api/console/tasks/capture", json={
        "mac": BUILD, "disk": "sda", "name": "Win11 lab", "folder": "Lab", **(body_extra or {})})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _q(kiosk_env, sql, *args):
    return kiosk_env["rt"].conn.execute(sql, args).fetchone()


def _boot(kiosk_env, mac=BUILD):
    assert kiosk_env["anon"].get(f"/boot/menu?mac={mac}").status_code == 200


def _hello_task(kiosk_env):
    return kiosk_env["anon"].post("/api/v1/agent/hello", json=hello_body(BUILD)).json()["task"]


def test_a_gui_capture_that_did_not_start_is_cancelled_by_the_boot(kiosk_env):
    _, gui = _env(kiosk_env)
    tid = _capture(gui)                     # the GUI's body says nothing about origin
    _boot(kiosk_env)
    row = _q(kiosk_env, "SELECT state, error FROM tasks WHERE id = ?", tid)
    assert (row["state"], row["error"]) == ("cancelled", REASON)
    journal = [(r["event"], r["detail"]) for r in
               kiosk_env["rt"].conn.execute("SELECT event, detail FROM journal").fetchall()]
    assert ("task_boot_cancel", f"{tid} {BUILD} (capture): {REASON}") in journal
    # the agent that comes up is not handed it: back to the login, not a capture
    assert _hello_task(kiosk_env) is None


def test_the_body_cannot_talk_a_gui_order_out_of_it(kiosk_env):
    """The channel decides: a GUI order whose body claims 'console' is still
    the GUI's, and is cancelled by the boot."""
    _, gui = _env(kiosk_env)
    tid = _capture(gui, {"origin": "console"})
    _boot(kiosk_env)
    assert _q(kiosk_env, "SELECT state FROM tasks WHERE id = ?", tid)["state"] == "cancelled"


def test_a_console_capture_survives_the_boot_it_was_waiting_for(kiosk_env):
    console, _ = _env(kiosk_env)
    tid = _capture(console, {"origin": "build_gui"})   # nor can a body talk a console order into it
    _boot(kiosk_env)
    assert _q(kiosk_env, "SELECT state FROM tasks WHERE id = ?", tid)["state"] == "pending"
    assert _hello_task(kiosk_env)["id"] == tid


def test_a_gui_task_that_already_runs_is_not_cancelled_by_a_boot(kiosk_env):
    """Only ``pending`` -- the agent had not begun. A running task is the
    agent's to report (its own failure path), not this guard's."""
    _, gui = _env(kiosk_env)
    tid = _capture(gui)
    kiosk_env["rt"].conn.execute("UPDATE tasks SET state = 'running' WHERE id = ?", (tid,))
    kiosk_env["rt"].conn.commit()
    _boot(kiosk_env)
    assert _q(kiosk_env, "SELECT state FROM tasks WHERE id = ?", tid)["state"] == "running"


def test_another_machines_boot_does_not_touch_it(kiosk_env):
    _, gui = _env(kiosk_env)
    tid = _capture(gui)
    _boot(kiosk_env, CLONER)
    assert _q(kiosk_env, "SELECT state FROM tasks WHERE id = ?", tid)["state"] == "pending"


def _direct(client, kiosk_env):
    cloner_hello(kiosk_env["anon"], CLONER, ["S1"])
    r = client.post("/api/console/room", json={
        "source": {"kind": "build_disk", "mac": BUILD, "disk": "sda"},
        "target_slots": [{"mac": CLONER, "ports": [1]}]})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_gui_direct_send_is_cancelled_with_its_round_and_wave(kiosk_env):
    """A direct send is a task AND a round with an open wave the drawers wait
    in: the task alone would leave a wave nobody will ever send to."""
    _, gui = _env(kiosk_env)
    opened = _direct(gui, kiosk_env)
    _boot(kiosk_env)
    task = _q(kiosk_env, "SELECT state, error FROM tasks WHERE id = ?", opened["task_id"])
    assert (task["state"], task["error"]) == ("cancelled", REASON)
    assert _q(kiosk_env, "SELECT state FROM room_rounds WHERE id = ?", opened["id"])["state"] == "closed"
    assert _q(kiosk_env, "SELECT state FROM sessions WHERE id = ?",
              opened["wave_session_id"])["state"] == "closed"


def test_a_console_direct_send_keeps_its_round_through_the_boot(kiosk_env):
    console, _ = _env(kiosk_env)
    opened = _direct(console, kiosk_env)
    _boot(kiosk_env)
    assert _q(kiosk_env, "SELECT state FROM tasks WHERE id = ?", opened["task_id"])["state"] == "pending"
    assert _q(kiosk_env, "SELECT state FROM room_rounds WHERE id = ?", opened["id"])["state"] == "active"
