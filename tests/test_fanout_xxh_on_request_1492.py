"""#1492 -- fanout hashes XXH64 only when -H asked for it.

The room round (`drawers.sh`) and the capture (`capture.sh`) run
`fanout -S <sha> -P <counter>`, no -H. Until #1492 every chunk still went
through XXH64 in the main read loop (`fanout.c`, the line after
`stdin_got += got`), and the digest was thrown away at EOF because nobody
asked for it: a second full pass over every byte, on the same thread that
hashes SHA-256 and copies to every drawer (149MB/s ceiling on the i5-650,
CHANGELOG.md, cloner 2, 15/10).

What is pinned here is invisible from outside -- the files written are the
same either way -- so two checks look at it from the two places that can:

* the source: the only XXH64 update of the stream in `main()` is guarded by
  `hash_path`. Runs everywhere, Windows included.
* the binary: built with gcov, run without -H, the XXH64 update function is
  never entered; with -H it is. Needs gcc + gcov (Linux/CI), declared skip
  elsewhere (#52).

The -H/-S output itself is covered in test_fanout.py
(`test_xxh64_and_sha256_are_written_together`,
`test_hash_and_stream_progress_cover_every_input_byte`).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "agent" / "fanout.c"

needs_gcov = requires_native("gcc", "gcov", posix=True, why="fanout + gcov צריכים gcc ו-POSIX")


def _main_body() -> str:
    text = SOURCE.read_text(encoding="utf-8")
    return text[text.index("int main(int argc, char **argv)"):]


def test_the_stream_xxh64_update_in_main_is_guarded_by_dash_h():
    calls = [line for line in _main_body().splitlines() if "xxh_update(&hash," in line]
    assert calls, "no XXH64 update of the stream in main() -- did -H stop hashing?"
    unguarded = [line for line in calls
                 if not re.match(r"^\s*if \(hash_path\) xxh_update\(&hash,", line)]
    assert unguarded == [], f"XXH64 computed without -H: {unguarded}"


def test_the_sha256_update_stays_guarded_by_dash_s():
    """The same rule on the other hash, so a refactor cannot swap one leak for another."""
    calls = [line for line in _main_body().splitlines() if "sha_update(&sha," in line]
    assert calls and all(re.match(r"^\s*if \(sha_path\) sha_update\(&sha,", line)
                         for line in calls), calls


def _gcov_lines_executed(build: Path, function: str) -> float:
    """Percent of `function`'s lines that ran, from `gcov -f -n`."""
    done = subprocess.run(["gcov", "-f", "-n", "-o", str(build), str(SOURCE)],
                          cwd=build, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=True)
    lines = done.stdout.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"Function '{function}'":
            match = re.match(r"Lines executed:\s*([0-9.]+)%", lines[i + 1].strip())
            assert match, done.stdout
            return float(match.group(1))
    raise AssertionError(f"gcov printed no summary for {function}:\n{done.stdout}")


def _run_with_coverage(tmp_path: Path, flags: list[str]) -> tuple[Path, bytes]:
    build = tmp_path / "build"
    build.mkdir()
    # Compile and link apart: the object (and so fanout.gcno/.gcda) is named
    # by -o, which is what `gcov -o <dir> fanout.c` looks for.
    subprocess.run(["gcc", "-O0", "--coverage", "-c", "-o", str(build / "fanout.o"), str(SOURCE)],
                   check=True, stdin=subprocess.DEVNULL)
    subprocess.run(["gcc", "--coverage", "-o", str(build / "fanout"), str(build / "fanout.o")],
                   check=True, stdin=subprocess.DEVNULL)
    data = bytes(range(256)) * 16384            # 4 MiB: four READ_CHUNKs
    out = tmp_path / "out.bin"
    out.write_bytes(b"")                        # fanout opens targets, never creates them
    done = subprocess.run([str(build / "fanout"), *flags, str(4 * 1024 * 1024), str(out)],
                          input=data, capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert out.read_bytes() == data
    return build, data


@needs_gcov
def test_without_dash_h_the_xxh64_update_never_runs(tmp_path):
    sha = tmp_path / "sha.out"
    build, data = _run_with_coverage(tmp_path, ["-S", str(sha)])
    # The SHA-256 that was asked for is still there, and right.
    assert sha.read_text() == hashlib.sha256(data).hexdigest() + "\n"
    assert _gcov_lines_executed(build, "xxh_update") == 0.0


@needs_gcov
def test_with_dash_h_the_xxh64_update_runs(tmp_path):
    """The positive side: the gcov reading can say "it ran" -- otherwise 0%
    above would prove nothing."""
    hash_file = tmp_path / "xxh.out"
    build, _ = _run_with_coverage(tmp_path, ["-H", str(hash_file)])
    assert re.fullmatch(r"[0-9a-f]{16}\n", hash_file.read_text())
    assert _gcov_lines_executed(build, "xxh_update") > 0.0
