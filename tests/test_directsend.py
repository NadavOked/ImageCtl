"""‏#715 — הפצה ישירה ממחשב הבנייה: הסוכן כמקור המולטיקאסט.

השרת נשאר המתזמר; מחשב הבנייה מריץ `udp-sender` על הדיסק שלו. מה
שנבדק כאן הוא שהשידור מהסוכן **זהה** לשידור מהשרת בכל דגל שקובע את
התנהגות הזרם — אחרת מקבל שכוונן ל-`--retries-until-drop 200` של השרת
(‏#437) היה מקבל ברירת מחדל אחרת ממקור אחר, והכשל היה נראה כמו רשת.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from test_agent import AGENT, BASH, posix, sh
from test_timeouts import make_stubs
from native import requires_native

pytestmark = requires_native(("bash", BASH))

#: ‏udp-sender מזויף שרושם את ה-argv שלו ובולע את stdin.
RECORD_ARGV = (
    '#!/bin/sh\n'
    'printf "%s\n" "$@" > "$UDP_ARGV"\n'
    'cat > /dev/null\n'
    'exit 0\n'
)


def _run_sender(tmp_path: Path, *args: str) -> list[str]:
    box = tmp_path / "box"
    run = box / "run"
    run.mkdir(parents=True)
    argv = box / "argv.txt"
    quoted = " ".join(shlex.quote(a) for a in args)
    sh(
        make_stubs(box / "stubs", {"udp-sender": RECORD_ARGV})
        + f"export RUN_DIR={posix(run)!r} IFACE=eth0 UDP_ARGV={posix(argv)!r}; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/directsend.sh; "
        f"printf 'bytes' | direct_udp_send {quoted}"
    )
    return argv.read_text(encoding="utf-8").splitlines()


def _flags(argv: list[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    i = 0
    while i < len(argv):
        word = argv[i]
        assert word.startswith("--"), argv
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            out[word] = argv[i + 1]
            i += 2
        else:
            out[word] = None
            i += 1
    return out


def test_the_agent_sends_with_the_servers_own_flags(tmp_path):
    """אותם דגלים בדיוק כמו `SenderEngine.command_for` — חוץ מ-`--file`
    (הזרם מגיע ב-stdin) ו-`--interface` (הכרטיס של מחשב הבנייה)."""
    from server.sender import SenderEngine

    engine = SenderEngine(library=None, portbase=31200, max_wait=120,
                          start_timeout=180, retries_until_drop=200,
                          max_bitrate=None)
    server_flags = _flags(engine.command_for(Path("p1.pcl.zst"), 2)[1:])
    agent_flags = _flags(_run_sender(tmp_path, "31200", "2", "120", "180", "200", ""))

    del server_flags["--file"]
    assert agent_flags.pop("--interface") == "eth0"
    assert agent_flags == server_flags


def test_max_bitrate_is_passed_only_when_the_server_set_one(tmp_path):
    with_cap = _flags(_run_sender(tmp_path / "cap", "31200", "1", "120", "180", "200", "800m"))
    assert with_cap["--max-bitrate"] == "800m"
    without = _flags(_run_sender(tmp_path / "nocap", "31200", "1", "120", "180", "200", ""))
    assert "--max-bitrate" not in without


# --- ‏direct_send_run: שתי קריאות, מניפסט בלי העלאה, sha על החוט ---------------

import hashlib
import json
import shutil

from test_agent import REPO
from test_capture_refusals import GPT_DISK, ONE_PARTITION
from test_timeouts import run_sh

MAC = "aa:bb:cc:00:00:10"
TASK = "tsk_715"

#: ‏partclone מזויף: מוציא 4096 בייטים שנקבעים ב-$PCL_BYTE; קריאה שנייה
#: יכולה להוציא משהו אחר (‏PCL_SECOND) כדי לדמות דיסק שהשתנה בין הקריאות.
PARTCLONE = (
    '#!/bin/sh\n'
    'n=$(cat "$PCL_COUNT" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$PCL_COUNT"\n'
    'b="$PCL_BYTE"; [ "$n" -ge 2 ] && [ -n "$PCL_SECOND" ] && b="$PCL_SECOND"\n'
    'head -c 4096 /dev/zero | tr "\\000" "$b"\n'
    'exit 0\n'
)

#: ‏udp-sender מזויף: רושם argv, בולע את הזרם, ומדפיס את שורת ההתחלה
#: כמו האמיתי — אלא אם UDP_SILENT: אז יוצא 0 בלי לשדר (#438).
UDP_SENDER = (
    '#!/bin/sh\n'
    'printf "%s\n" "$@" >> "$UDP_ARGV"\n'
    'cat > /dev/null\n'
    '[ -n "$UDP_SILENT" ] || echo "Starting transfer: 0000000"\n'
    'exit 0\n'
)

#: ‏curl מזויף: רושם את ה-argv שלו (איזה URL, אילו דגלים) ועונה 0.
CURL_RECORD = (
    '#!/bin/sh\n'
    'printf "%s\n" "$@" >> "$CURL_ARGV"\n'
    'echo "---" >> "$CURL_ARGV"\n'
    'exit 0\n'
)


#: ‏jq אינו מותקן בתחנת הפיתוח (ווינדוס). במקום דילוג שקט — תחליף צר
#: בפייתון, **רק** לביטויים שמסלול ההפצה הישירה מריץ (json_get, ‏manifest_plan,
#: ‏.partitions|length, בלוק המולטיקאסט); כל ביטוי אחר יוצא 5 כמו jq על
#: שגיאה. במעבדה (jq אמיתי ב-PATH) התחליף אינו נכנס לתמונה.
JQ_SHIM_PY = (REPO / "tests" / "jq_shim.py")
JQ_STUB = '#!/bin/sh\nexec python "' + posix(JQ_SHIM_PY) + '" "$@"\n'


def _answer(session_state="running", task=TASK, receivers=2, bitrate=None):
    return {"schema": 1, "known": True, "role": "build", "task": {
        "id": task, "type": "direct_send", "disk": "sda", "image_id": "live_aabbcc",
        "token": "t" * 48,
        "direct": {"session_id": "ses_1", "session_state": session_state,
                   "receivers": receivers,
                   "multicast": {"portbase": 31200, "min_receivers": receivers,
                                 "max_wait": 120, "start_timeout": 180,
                                 "retries_until_drop": 200, "max_bitrate": bitrate}}}}


def direct_run(tmp_path, *, answer=None, env=None, udp_silent=False):
    box = tmp_path / "box"
    dev, run = box / "dev", box / "run"
    dev.mkdir(parents=True); run.mkdir(parents=True)
    (dev / "sda").write_bytes(GPT_DISK)
    (dev / "sda1").write_bytes(bytes(512))
    queue = box / "sys/block/sda/queue"; queue.mkdir(parents=True)
    (queue / "logical_block_size").write_text("512\n")
    nodes = box / "nodes"
    nodes.write_text(f"{posix(dev)}/sda\n{posix(dev)}/sda1\n", newline="\n")
    (box / "answer.json").write_text(json.dumps(answer or _answer()), encoding="utf-8")
    envs = {"RUN_DIR": posix(run), "DEVROOT": posix(dev), "SYSROOT": posix(box),
            "SERVER": "http://s", "IFACE": "eth0", "MAC": MAC, "TASK_TOKEN": "t" * 48,
            "RESP": posix(box / "resp.json"), "IMAGECTL_TEST": "1",
            "PCL_COUNT": posix(box / "pcl.count"), "PCL_BYTE": "A", "PCL_SECOND": "",
            "UDP_ARGV": posix(box / "udp.argv"), "CURL_ARGV": posix(box / "curl.argv"),
            "UDP_SILENT": "1" if udp_silent else "", "DIRECT_POLL_S": "0",
            **(env or {})}
    exports = "".join(f"export {k}={v!r}; " for k, v in envs.items())
    stubs = {"sgdisk": ONE_PARTITION, "partclone.dd": PARTCLONE,
             "udp-sender": UDP_SENDER, "curl": CURL_RECORD}
    if shutil.which("jq") is None:
        stubs["jq"] = JQ_STUB
    libs = " ".join(f". {posix(AGENT)}/lib/{n}.sh;" for n in (
        "common", "sysinfo", "waits", "jsonq", "progress", "restore", "manifest",
        "bootca", "hibernation", "capture", "directsend", "ui"))
    out = run_sh(
        make_stubs(box / "stubs", stubs) + exports + libs
        + f' node_is_block() {{ grep -qxF "$1" {posix(nodes)!r} 2>/dev/null; }}; '
        f'send_hello() {{ cp {posix(box / "answer.json")!r} "$RESP"; }}; '
        f"direct_send_run {TASK} sda > {posix(box)}/direct.out 2>&1; "
        'echo "rc=$?"; '
        f"build_progress '' {MAC} {TASK} > {posix(box)}/progress.json"
    )
    return box, run, out


def _progress(box):
    return json.loads((box / "progress.json").read_text(encoding="utf-8"))


def test_the_disk_is_read_twice_hashed_once_on_the_wire_and_nothing_is_uploaded(tmp_path):
    box, run, out = direct_run(tmp_path)
    assert out.strip().endswith("rc=0"), out + (run / "agent.log").read_text("utf-8", "replace")
    manifest = json.loads((run / "new-manifest.json").read_text("utf-8"))
    expected = hashlib.sha256(b"A" * 4096).hexdigest()
    assert manifest["partitions"][0]["sha256"] == expected
    # הקריאה השנייה גובבה על החוט והושוותה למניפסט
    assert (run / "dsha.1").read_text("utf-8").split()[0] == expected
    assert (box / "pcl.count").read_text().strip() == "2"
    # שום בייט לא הועלה: curl נקרא רק ל-PUT של המניפסט, בלי -T ובלי /capture/
    curl = (box / "curl.argv").read_text("utf-8")
    assert "/api/v1/direct/tsk_715/manifest" in curl
    assert "-T" not in curl.splitlines() and "/capture/" not in curl
    # ‏udp-sender קיבל את ערכי השרת, על הכרטיס שלנו, בלי --file
    udp = (box / "udp.argv").read_text("utf-8").splitlines()
    assert "--min-receivers" in udp and udp[udp.index("--min-receivers") + 1] == "2"
    assert udp[udp.index("--interface") + 1] == "eth0" and "--file" not in udp
    report = _progress(box)
    assert report["state"] == "sending" and report["targets"][0]["state"] != "failed"
    assert report["targets"][0]["bytes_total"] == manifest["total_compressed_bytes"]


def test_a_sender_that_exits_zero_without_transferring_is_a_failure(tmp_path):
    """‏#438 בצד המקור: udp-sender שפג לו start-timeout יוצא 0 בלי מקבל."""
    box, run, out = direct_run(tmp_path, udp_silent=True)
    assert out.strip().endswith("rc=1"), out
    report = _progress(box)
    assert report["state"] == "failed"
    assert "no cloning machine joined" in report["targets"][0]["error"]


def test_bytes_that_differ_from_the_manifest_fail_at_the_source_by_name(tmp_path):
    box, run, out = direct_run(tmp_path, env={"PCL_SECOND": "B"})
    assert out.strip().endswith("rc=1"), out
    report = _progress(box)
    assert "differ from the manifest" in report["targets"][0]["error"]


def test_a_closed_wave_or_a_task_that_is_no_longer_ours_does_not_send(tmp_path):
    for name, answer in (("closed", _answer("closed")), ("other", _answer(task="tsk_x"))):
        box, run, out = direct_run(tmp_path / name, answer=answer)
        assert out.strip().endswith("rc=1"), out
        assert not (box / "udp.argv").exists(), name
        assert "did not start" in _progress(box)["targets"][0]["error"]
