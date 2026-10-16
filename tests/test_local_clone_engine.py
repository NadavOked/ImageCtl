"""#1322 — the local clone engine on real block devices: sparse image files on
loop devices (``localclone-fixture.sh``), root through ``sudo -n``. The
Testrunner only; elsewhere the module is a declared skip (``native.py``).

The source is a Windows-like disk with a Linux root last: ESP (FAT32), MSR
(no filesystem -- copied whole), Windows (NTFS, shrinkable), Recovery (NTFS,
attributes), root (ext4, the one that grows). The checks read the targets
back independently of the engine -- ``sgdisk`` and the files' own sha256 --
so a wrong engine cannot vouch for itself:

* identical: the disk GUID, every partition's type, unique GUID, name,
  attributes, start and size; the files; the raw MSR bytes;
* a larger target: the last system partition grows to the end, the rest is
  identical;
* a smaller target: refused when the data does not fit, refused when the fit
  cannot be checked; with the server up it fits through the temporary shrink
  of the source, and the source comes back to its size with its files intact;
* a target whose writes fail fails alone, visibly, and the others finish;
* a stop request ends the copy: the targets fail, the source is untouched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "agent" / "lib"
FIXTURE = REPO / "tests" / "localclone-fixture.sh"
SBIN = "/usr/sbin:/sbin"


def _sudo_ok() -> bool:
    if os.name == "nt" or not shutil.which("sudo"):
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True, stdin=subprocess.DEVNULL).returncode == 0


def _tool(name: str) -> str | None:
    return shutil.which(name) or shutil.which(name, path=SBIN)


pytestmark = requires_native(
    ("sudo -n", _sudo_ok()),
    *[(t, _tool(t)) for t in ("losetup", "sgdisk", "partclone.ntfs", "partclone.fat", "partclone.ext4",
                              "partclone.dd", "ntfsresize", "mkntfs", "mkfs.vfat", "mkfs.ext4", "ntfs-3g",
                              "pv", "gcc", "jq", "curl")],
    posix=True,
    why="loop devices and partclone as root: the Testrunner",
)

LIBS = ("common", "jsonq", "waits", "sysinfo", "progress", "restore", "diskfit", "expand", "grow", "drawers",
        "verdict", "failmark", "ntfsboot", "manifest", "hibernation", "shrinkplan", "shrink", "shrinkmem", "smart",
        "lcinv", "lcplan", "lcsnap", "lccopy", "localclone")

DRIVER = r'''
set -u
F=$1; TARGETS=$2; SHRINK=$3
export RUN_DIR=$F/run LIB_DIR=%(lib)s IMAGECTL_TEST=1 MAC=aa:bb:cc:dd:ee:ff
rm -rf "$RUN_DIR"; mkdir -p "$RUN_DIR"
# #1354: the fixture's disks are MiB-sized, so no target could keep 20 GB free.
# 0 = the console's "rule off" -- the copy itself is what this file checks;
# the rule has its own tests (test_min_free_1354.py, lc_verdict_for).
echo 0 > "$RUN_DIR/min_free_gb"
# DEVROOT is the test's own directory (test_tests_never_touch_host_dev): links
# to the fixture's loop devices and their partitions, and nothing else -- the
# engine cannot reach a disk of the host.
export DEVROOT=$F/dev; mkdir -p "$DEVROOT"
for d in $(awk '{ print $2 }' "$F/devs"); do n=${d#/dev/}; ln -sf "$d" "$DEVROOT/$n"
  for p in 1 2 3 4 5 6 7 8 9; do ln -sf "${d}p$p" "$DEVROOT/${n}p$p"; done; done
# #1466: the RAM view's own nodes (device-mapper and brd) -- RAM, not a disk of the host
ln -sfn /dev/mapper "$DEVROOT/mapper"; for i in 0 1 2 3; do ln -sf "/dev/ram$i" "$DEVROOT/ram$i"; done
%(sources)s
dev() { awk -v n="$1" '$1 == n { sub("/dev/", "", $2); print $2 }' "$F/devs"; }
SRC=$(dev src)
disk_serial() { echo "SER-$1"; }                 # loop devices have none; the shrink record needs one
list_drawers() { echo "$SRC"; for n in same big small tiny full; do dev "$n"; done; }
mkdir -p "$LC"
lc_inventory
lc_measure "$SRC"
for n in $TARGETS; do echo "verdict $n $(lc_verdict_for "$SRC" "$(dev "$n")")"; done
T=""; for n in $TARGETS; do T="$T $(dev "$n")"; done
# המשגיח: תקרה (600 x 0.2s) ונהרג אחרי lc_run — lc_run שנכשל לפני "copy" השאיר
# אותו חי, מחזיק את הצינור, והטסט נתקע עד timeout=1500 (15/10, אחרי #1433)
_stopper=""
[ -z "${STOP_AT:-}" ] || { ( i=0; while [ $i -lt 600 ]; do [ "$(cat "$LC/run/cur" 2>/dev/null)" = "$STOP_AT|copy" ] && { touch "$LC/stop.req"; exit 0; }; i=$((i + 1)); sleep 0.2; done ) & _stopper=$!; }
lc_run "$SRC" "$T" "$SHRINK"
[ -z "$_stopper" ] || kill "$_stopper" 2>/dev/null || :
echo "phase $(cat "$LC/phase")"
for n in $TARGETS; do d=$(dev "$n"); t=$RUN_DIR/targets/$d
  echo "target $n|$(cat "$t/state")|$(cat "$t/error" 2>/dev/null | tr '\n' ' ')|$(cat "$t/lc_ok" 2>/dev/null | tr '\n' ' ')|$(cat "$t/lc_table" 2>/dev/null)"; done
echo "restore $(cat "$LC/run/source_restore" 2>/dev/null)"
echo "report $(cat "$LC/run/report" 2>/dev/null)"
'''


class _Server:
    """The server's agent routes the local clone touches: state (it answers),
    shrink-open/close (#926) and local-clone (the report)."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []
        posts = self.posts

        class H(BaseHTTPRequestHandler):
            def _send(self, body: dict, code: int = 200) -> None:
                data = json.dumps(body).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                self._send({"known": True})

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                posts.append((self.path.rsplit("/", 1)[-1], body))
                self._send({"ok": True, "id": 7})

            def log_message(self, *_a):
                pass

        self.http = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.http.shutdown()


def _root(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["sudo", "-n", "env", f"PATH={os.environ.get('PATH', '')}:{SBIN}", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def fanout_bin(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("lcbin")
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-o", str(d / "fanout"), str(REPO / "agent" / "fanout.c")],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    return d


@pytest.fixture
def disks(tmp_path):
    fx = tmp_path / "fx"
    up = _root("sh", str(FIXTURE), "setup", str(fx), timeout=300)
    try:
        assert up.returncode == 0, up.stdout + up.stderr
        devs = dict(line.split() for line in (fx / "devs").read_text().splitlines())
        yield fx, devs
    finally:
        _root("sh", str(FIXTURE), "teardown", str(fx), timeout=120)


def _engine(tmp_path, fx: Path, fanout: Path, targets: str, shrink: int, server: str = "", stop_at: str = "",
            env: tuple[str, ...] = ()) -> dict:
    sources = "\n".join(f'. "$LIB_DIR/{n}.sh"' for n in LIBS)
    script = tmp_path / "drive.sh"
    script.write_text(DRIVER % {"lib": LIB, "sources": sources}, newline="\n")
    run = _root(f"PATH={fanout}:{os.environ.get('PATH', '')}:{SBIN}", f"SERVER={server}", f"STOP_AT={stop_at}",
                "LC_STALL_S=120", *env, "sh", str(script), str(fx), targets, str(shrink), timeout=1500)
    out = {"raw": run.stdout + run.stderr, "verdict": {}, "target": {}}
    for line in run.stdout.splitlines():
        if line.startswith("verdict "):
            _v, name, rest = line.split(" ", 2); out["verdict"][name] = rest.split("|")[0]
        elif line.startswith("target "):
            name, state, error, ok, table = line[7:].split("|", 4)
            out["target"][name] = {"state": state, "error": error, "ok": ok.split(), "table": table}
        elif line.startswith(("phase ", "restore ", "report ")):
            k, _, v = line.partition(" "); out[k] = v
    assert run.returncode == 0, out["raw"][-4000:]
    return out


def _table(dev: str) -> dict:
    """The table read back with sgdisk, independently of the engine."""
    p = _root("sgdisk", "-p", dev).stdout
    disk_guid = next(l.split(": ")[1].strip() for l in p.splitlines() if l.startswith("Disk identifier"))
    parts = {}
    for idx in [l.split()[0] for l in p.splitlines() if l.strip()[:1].isdigit() and len(l.split()) > 5]:
        info = dict(l.split(": ", 1) for l in _root("sgdisk", "-i", idx, dev).stdout.splitlines() if ": " in l)
        parts[int(idx)] = {k: info[k].split(" (")[0].strip() for k in
                           ("Partition GUID code", "Partition unique GUID", "First sector", "Partition size",
                            "Attribute flags", "Partition name")}
    return {"guid": disk_guid, "parts": parts}


def _files(dev: str, fx: Path) -> list[str]:
    out = _root("sh", str(FIXTURE), "files", dev, str(fx))
    lines = sorted(out.stdout.splitlines())
    assert lines and not any("mount-failed" in l for l in lines), out.stdout + out.stderr
    return lines


def _no_table(dev: str) -> bool:
    head = _root("dd", f"if={dev}", "bs=512", "skip=1", "count=1", "status=none")
    return "EFI PART" not in head.stdout


def _first_last_md5(part: str) -> tuple[str, str]:
    """md5 of a partition's first and last 512 bytes, read with dd -- not by the engine."""
    out = _root("sh", "-c", f'n=$(blockdev --getsz {part}) && dd if={part} bs=512 count=1 status=none | md5sum'
                            f' && dd if={part} bs=512 skip=$((n - 1)) count=1 status=none | md5sum').stdout.split()
    assert len(out) == 4, out
    return out[0], out[2]


def _size(part: dict) -> int:
    return int(part["Partition size"].split()[0])


def test_identical_and_larger_copies_a_failing_target_alone_and_refusals(tmp_path, disks, fanout_bin):
    fx, devs = disks
    src_table, src_files = _table(devs["src"]), _files(devs["src"], fx)
    res = _engine(tmp_path, fx, fanout_bin, "same big small tiny full", 0)
    v, t = res["verdict"], res["target"]
    assert v == {"same": "fits", "big": "fits", "small": "unchecked", "tiny": "short", "full": "fits"}, res["raw"][-3000:]
    assert t["same"]["state"] == "done" and t["big"]["state"] == "done", res["raw"][-3000:]
    assert t["same"]["ok"] == ["1", "2", "3", "4", "5"] and t["big"]["ok"] == ["1", "2", "3", "4", "5"]
    # identical, read back by sgdisk: GUIDs, types, names, attributes, places and sizes
    same = _table(devs["same"])
    assert same == src_table
    big = _table(devs["big"])
    assert big["guid"] == src_table["guid"]
    for i in (1, 2, 3, 4):
        assert big["parts"][i] == src_table["parts"][i], i
    grown, was = big["parts"][5], src_table["parts"][5]
    assert {k: grown[k] for k in grown if k != "Partition size"} == {k: was[k] for k in was if k != "Partition size"}
    assert _size(grown) > _size(was) + 2_000_000, (grown, was)            # +1.5 GB to the end
    assert t["big"]["table"].startswith("5|")
    # the content: every file, and the raw bytes of the partition with no filesystem
    assert _files(devs["same"], fx) == src_files and _files(devs["big"], fx) == src_files
    for d in ("same", "big"):
        assert _root("cmp", "-n", "16777216", devs["src"] + "p2", devs[d] + "p2").returncode == 0, d
    # #1487: the NTFS backup boot sector (the partition's last sector; mkntfs
    # fills the partition) -- partclone -r does not write it. The metal check.
    for d in ("src", "same", "big"):
        for p in ("p3", "p4"):
            first, last = _first_last_md5(devs[d] + p)
            assert first == last, (d, p, first, last)
    # refused before a byte: not checked (no server for the shrink) and too small
    assert t["small"]["state"] == "failed" and "לא נבדק" in t["small"]["error"] and _no_table(devs["small"])
    assert t["tiny"]["state"] == "failed" and "חסרים" in t["tiny"]["error"] and _no_table(devs["tiny"])
    # the target whose writes fail fails alone, with the partition and the cause
    assert t["full"]["state"] == "failed" and "מחיצה" in t["full"]["error"], t["full"]
    # the source was only read
    assert _table(devs["src"]) == src_table and _files(devs["src"], fx) == src_files
    assert res["phase"] == "done" and res["report"].startswith("failed|")   # no server: said, not hidden


def test_no_ram_for_a_view_refuses_the_smaller_target_and_writes_nothing(tmp_path, disks, fanout_bin):
    """#1466 option B (owner, 16/10): no RAM left for a view once the reserve is
    taken -> the smaller target is refused by name, before a byte; the source
    is never shrunk in place, and the target that fits is copied as usual."""
    fx, devs = disks
    src_table, src_files = _table(devs["src"]), _files(devs["src"], fx)
    src_sha = _root("sha256sum", devs["src"]).stdout.split()[0]
    srv = _Server()
    try:
        res = _engine(tmp_path, fx, fanout_bin, "small same", 1, server=srv.url,
                      env=("LC_SNAP_RESERVE_MB=100000000",))
    finally:
        srv.close()
    v, t = res["verdict"], res["target"]
    assert v == {"small": "shrink", "same": "fits"}, res["raw"][-3000:]
    assert t["small"]["state"] == "failed" and "שמורים להעתקה עצמה" in t["small"]["error"], res["raw"][-3000:]
    assert _no_table(devs["small"])
    assert t["same"]["state"] == "done" and _files(devs["same"], fx) == src_files
    assert _root("sha256sum", devs["src"]).stdout.split()[0] == src_sha            # not one byte written
    assert _table(devs["src"]) == src_table
    assert "shrink-open" not in [k for k, _b in srv.posts]
    report = dict(srv.posts)["local-clone"]
    assert report["shrink_path"] == "refused" and report["shrunk"] is False, report


@pytest.mark.skipif(_tool("dmsetup") is None, reason="dmsetup is not installed: the RAM view (#1466) cannot be built here")
def test_a_smaller_target_through_a_ram_view_leaves_the_source_bytes_identical(tmp_path, disks, fanout_bin):
    """#1466 (owner, 16/10): dm-snapshot over the source partition, the store on brd --
    the source disk is only read: every byte of it is the same after the copy."""
    fx, devs = disks
    src_table, src_files = _table(devs["src"]), _files(devs["src"], fx)
    src_sha = _root("sha256sum", devs["src"]).stdout.split()[0]
    srv = _Server()
    try:
        res = _engine(tmp_path, fx, fanout_bin, "small same", 1, server=srv.url)
    finally:
        srv.close()
    v, t = res["verdict"], res["target"]
    assert v["small"] == "shrink", res["raw"][-3000:]
    assert t["small"]["state"] == "done" and t["same"]["state"] == "done", res["raw"][-3000:]
    assert _size(_table(devs["small"])["parts"][3]) < _size(src_table["parts"][3])   # the compacted layout
    assert _files(devs["small"], fx) == src_files and _files(devs["same"], fx) == src_files
    assert _root("sha256sum", devs["src"]).stdout.split()[0] == src_sha            # not one byte written
    assert _table(devs["src"]) == src_table
    report = dict(srv.posts)["local-clone"]
    assert report["shrink_path"] == "snapshot" and report["snapshot_teardown"] == "ok", report
    assert report["shrunk"] is False and isinstance(report["relocations_mb"], int), report
    assert "shrink-open" not in [k for k, _b in srv.posts]                         # nothing to put back
    left = _root("dmsetup", "ls").stdout
    assert "lc-src-" not in left and not Path("/sys/module/brd").exists(), left


def test_without_the_confirmed_shrink_the_small_target_is_not_written(tmp_path, disks, fanout_bin):
    fx, devs = disks
    src_table = _table(devs["src"])
    srv = _Server()
    try:
        res = _engine(tmp_path, fx, fanout_bin, "small same", 0, server=srv.url)
    finally:
        srv.close()
    t = res["target"]
    assert res["verdict"]["small"] == "shrink"
    assert t["small"]["state"] == "failed" and "חלון האישור" in t["small"]["error"] and _no_table(devs["small"])
    assert t["same"]["state"] == "done"
    assert "shrink-open" not in [k for k, _b in srv.posts]                # the source was never touched
    assert _table(devs["src"]) == src_table


def test_a_stop_ends_the_copy_and_leaves_the_source(tmp_path, disks, fanout_bin):
    fx, devs = disks
    src_table, src_files = _table(devs["src"]), _files(devs["src"], fx)
    res = _engine(tmp_path, fx, fanout_bin, "same", 0, stop_at="3")
    t = res["target"]["same"]
    assert res["phase"] == "stopped", res["raw"][-3000:]
    assert t["state"] == "failed" and "נעצר" in t["error"], t
    assert _table(devs["src"]) == src_table and _files(devs["src"], fx) == src_files
