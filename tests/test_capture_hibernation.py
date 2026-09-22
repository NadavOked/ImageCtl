"""#651: reject hibernated NTFS before capture opens an upload."""

from test_agent import AGENT, posix, sh
from test_capture_refusals import (
    CURL_SINK,
    ONE_PARTITION,
    PIPE_INPUT,
    capture_run,
    refusal_reason,
)
from test_timeouts import make_stubs


def _reason(tmp_path, *, hiberfil=False, probe_rc=0):
    mount_body = 'mkdir -p "$4"\n'
    if hiberfil:
        mount_body += 'touch "$4/hiberfil.sys"\n'
    mount_body += 'exit 0\n'
    script = (
        make_stubs(
            tmp_path / "stubs",
            {
                "ntfs-3g.probe": f"#!/bin/sh\nexit {probe_rc}\n",
                "ntfs-3g": f"#!/bin/sh\n{mount_body}",
                "umount": "#!/bin/sh\nexit 0\n",
            },
        )
        + f". {posix(AGENT)}/lib/hibernation.sh; "
        + f"capture_ntfs_hibernation_reason /dev/fake {posix(tmp_path / 'mnt')}"
    )
    output = sh(script + '; rc=$?; printf "\\n__rc=%s\\n" "$rc"; exit 0')
    body, rc = output.rsplit("__rc=", 1)
    return int(rc.strip()), body.strip()


def _bitlocker(tmp_path, dd_body: str) -> str:
    run = tmp_path / "run"; run.mkdir(parents=True)
    script = (
        make_stubs(tmp_path / "stubs", {"dd": f"#!/bin/sh\n{dd_body}\n"})
        + f'RUN_DIR={posix(run)!r}; LOG_FILE={posix(run / "agent.log")!r}; '
        + f'. {posix(AGENT)}/lib/hibernation.sh; '
        + '_bitlocker_reason 1 ntfs /dev/fake'
    )
    return sh(script).strip()


def test_hiberfil_presence_alone_is_not_hibernation(tmp_path):
    """17/09, נמדד על מחשב הבנייה במעבדה: hiberfil.sys של 3.36GB קיים על כל
    ווינדוס שה-hibernation/fast-startup מופעל בו, גם אחרי כיבוי מלא
    (`ntfs-3g.probe` rc=0, `ntfsresize --info` נקי) — והקליטה סורבה "הדיסק
    מהובר" על דיסק נקי. המצב המהובר הוא בכותרת הקובץ, וזה בדיוק מה
    ש-probe rc=14 בודק. קובץ קיים + probe 0 = נקי."""
    with_file = _reason(tmp_path / "file", hiberfil=True)
    clean = _reason(tmp_path / "clean")
    assert with_file == (0, "")
    assert clean == (0, "")


def test_probe_dirty_or_hibernated_flags_are_reported(tmp_path):
    """Mutation caught: ignoring probe rc 14/15 would report these volumes clean."""
    for rc in (14, 15):
        result = _reason(tmp_path / str(rc), probe_rc=rc)
        assert result[0] == 1
        assert "הדיסק מהובר" in result[1]


def test_probe_failure_is_not_clean(tmp_path):
    """Principle 5: an unknown probe result is a separate refusal."""
    result = _reason(tmp_path, probe_rc=18)
    assert result[0] == 2
    assert "לא הצלחנו לבדוק" in result[1]


def test_bitlocker_header_has_detected_clean_and_unable_states(tmp_path):
    """#1130: a failed dd used to look exactly like a clean non-match."""
    unable = _bitlocker(tmp_path / "unable", "exit 7")
    clean = _bitlocker(tmp_path / "clean", "printf plain-header")
    encrypted = _bitlocker(tmp_path / "encrypted", "printf -- '-FVE-FS-'")
    assert "לא הצלחנו לבדוק" in unable and "dd rc=7" in unable
    assert clean == ""
    assert "BitLocker" in encrypted and "מוצפנת" in encrypted


def test_detection_commands_are_read_only():
    source = (AGENT / "lib" / "hibernation.sh").read_text(encoding="utf-8")
    assert "ntfs-3g.probe --readwrite" in source
    assert 'ntfs-3g -o rw' not in source          # 17/09: no mount at all any more
    assert "remove_hiberfile" not in source
    assert "ntfsfix" not in source


def test_hibernated_capture_stops_before_upload_but_clean_capture_proceeds(tmp_path):
    """Mutation caught: always-clean guard opens curl and creates a manifest for dirty input."""
    # ‏`_fs_of` מוחלף ל-ntfs, ולכן הזרם הוא partclone.ntfs האמיתי — על צומת
    # מחיצה שאינו קיים בקופסה הוא נופל ב-`clone: open ... error` (stage=pcl
    # rc=1) גם כשהשומר אישר. הסטאב הוא אותו PIPE_INPUT של בדיקות אמצע-הצינור.
    common = {"sgdisk": ONE_PARTITION, "curl": CURL_SINK, "partclone.ntfs": PIPE_INPUT}
    override = '_fs_of() { echo ntfs; }; '
    hibernated = (
        override
        + 'capture_ntfs_hibernation_reason() { '
        + 'echo "$CAPTURE_HIBERNATED_MESSAGE"; return 1; }; '
    )
    box, run, out = capture_run(tmp_path / "dirty", stubs=common, shell_pre=hibernated)
    reason = refusal_reason(box, run, out)
    assert "הדיסק מהובר" in reason
    assert not (run / "new-manifest.json").exists()

    box, run, out = capture_run(
        tmp_path / "clean",
        stubs=common,
        shell_pre=override + 'capture_ntfs_hibernation_reason() { return 0; }; ',
    )
    assert out.strip().endswith("rc=0"), out
    assert (run / "new-manifest.json").exists()
