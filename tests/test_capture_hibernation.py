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


def test_hiberfil_is_reported_but_a_clean_volume_is_not(tmp_path):
    """Mutation caught: always returning clean makes the hiberfil half fail."""
    dirty = _reason(tmp_path / "dirty", hiberfil=True)
    clean = _reason(tmp_path / "clean")
    assert dirty[0] == 1
    assert "הדיסק מהובר" in dirty[1]
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


def test_detection_commands_are_read_only():
    source = (AGENT / "lib" / "hibernation.sh").read_text(encoding="utf-8")
    assert "ntfs-3g.probe --readwrite" in source
    assert 'ntfs-3g -o ro' in source
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
