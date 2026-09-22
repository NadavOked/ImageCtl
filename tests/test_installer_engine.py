"""Live installer engine (#1190): preflight, contracts, and shimmed stages."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import sizelimit

REPO = Path(__file__).resolve().parent.parent
ENGINE = REPO / "installer" / "imagectl-install"
INSTALLER = REPO / "installer"
SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(SH is None, reason="a POSIX sh is required")
LINUX_ONLY = pytest.mark.skipif(os.name == "nt", reason="executable shims and block-device flow are Linux-only")

KEYS = (
    "disk", "role", "primary_url", "servers_if", "servers_mac", "servers_mode",
    "servers_addr", "servers_mask", "servers_gw", "servers_dns", "hostname", "admin_user", "admin_pass",
)


def _machine(tmp_path: Path) -> dict[str, Path | dict[str, str]]:
    root = tmp_path / "root"
    disk = root / "sys" / "block" / "sda"
    (disk / "device").mkdir(parents=True)
    (disk / "size").write_text("125829120\n", newline="\n")
    (disk / "removable").write_text("0\n", newline="\n")
    (disk / "device" / "model").write_text("VMware Virtual disk\n", newline="\n")
    (disk / "device" / "bus").write_text("sata\n", newline="\n")
    nic = root / "sys" / "class" / "net" / "ens33"
    nic.mkdir(parents=True)
    (nic / "address").write_text("00:50:56:01:02:01\n", newline="\n")
    proc = root / "proc"
    proc.mkdir()
    mounts = proc / "mounts"
    mounts.write_text("/dev/sr0 /cdrom iso9660 ro 0 0\n", newline="\n")
    dev = tmp_path / "dev"
    dev.mkdir()
    for name in ("sda", "sda1", "sda2"):
        (dev / name).touch()
    cdrom = tmp_path / "cdrom"
    (cdrom / "imagectl-src" / ".git").mkdir(parents=True)
    (cdrom / "imagectl-src" / "tools" / "iso").mkdir(parents=True)
    (cdrom / "imagectl-src" / "tools" / "iso" / "packages.txt").write_text(
        "busybox-static\n", newline="\n")
    (cdrom / "imagectl").mkdir()
    (cdrom / "imagectl" / "firstboot.sh").write_text("#!/bin/bash\n", newline="\n")
    (cdrom / "imagectl" / "imagectl-firstboot.service").write_text("[Service]\n", newline="\n")
    (cdrom / "imagectl-iso.json").write_text('{"tag":"test"}\n', newline="\n")
    (cdrom / "imagectl" / "root-password.hash").write_text("$6$salt$hash\n", newline="\n")
    (cdrom / "pool" / "main" / "imagectl").mkdir(parents=True)
    (cdrom / "pool" / "main" / "imagectl" / "x.deb").touch()
    (cdrom / "dists" / "trixie").mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    state = tmp_path / "state"
    env = os.environ.copy()
    env.update({
        "SYSROOT": str(root), "DEVROOT": str(dev), "PROC_MOUNTS": str(mounts),
        "CDROM": str(cdrom), "TARGET": str(target), "STATE_FILE": str(state),
        "BOOTSTRAP_LOG": str(tmp_path / "debootstrap.log"), "IMAGECTL_TEST": "1",
    })
    return {"root": root, "dev": dev, "cdrom": cdrom, "target": target,
            "state": state, "mounts": mounts, "env": env}


def _answers(path: Path, **changes: str) -> Path:
    data = {
        "disk": "/dev/sda", "role": "standalone", "primary_url": "",
        "servers_if": "ens33", "servers_mac": "00:50:56:01:02:01",
        "servers_mode": "dhcp", "servers_addr": "", "servers_mask": "",
        "servers_gw": "", "servers_dns": "", "hostname": "imagectl-ta",
        "admin_user": "admin", "admin_pass": "correct horse battery staple",
    }
    data.update(changes)
    path.write_text("".join(f"{key}={data[key]}\n" for key in KEYS), newline="\n")
    return path


def _run(box, answers: Path | None = None, *args: str, engine: Path = ENGINE):
    cmd = [SH, str(engine)]
    if answers is not None:
        cmd.append(str(answers))
    cmd.extend(args)
    return subprocess.run(cmd, text=True, capture_output=True, env=box["env"],
                          cwd=REPO, stdin=subprocess.DEVNULL)


def _shim(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, newline="\n")
    path.chmod(0o755)


def _command_shims(tmp_path: Path, env: dict[str, str], *, debootstrap_rc: int = 0) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls"
    common = f'printf "%s %s\\n" "${{0##*/}}" "$*" >> {calls}\n'
    for name in ("sgdisk", "blockdev", "mkfs.vfat", "mkfs.ext4", "mount", "umount",
                 "chroot", "grub-install", "reboot"):
        _shim(bindir / name, common + "exit 0\n")
    _shim(bindir / "debootstrap", common + f"echo bootstrap-output\nexit {debootstrap_rc}\n")
    dev = Path(env["DEVROOT"])
    _shim(bindir / "blkid", f'''case "$*" in
*"PTTYPE"*"{dev}/sda") echo gpt ;;
*"TYPE"*"{dev}/sda1") echo vfat ;;
*"TYPE"*"{dev}/sda2") echo ext4 ;;
*"UUID"*"{dev}/sda1") echo ESP-UUID ;;
*"UUID"*"{dev}/sda2") echo ROOT-UUID ;;
*) exit 2 ;;
esac
''')
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    return calls


@pytest.mark.parametrize("missing", KEYS)
@LINUX_ONLY
def test_each_missing_answer_is_named_and_no_disk_command_runs(tmp_path, missing):
    box = _machine(tmp_path)
    answers = _answers(tmp_path / "answers")
    lines = [line for line in answers.read_text(encoding="utf-8").splitlines() if not line.startswith(missing + "=")]
    answers.write_text("\n".join(lines) + "\n", newline="\n")
    calls = _command_shims(tmp_path, box["env"])
    got = _run(box, answers, "--dry-run")
    assert got.returncode == 2
    assert f"missing answer: {missing}" in got.stderr
    assert not calls.exists(), calls.read_text(encoding="utf-8") if calls.exists() else ""


@pytest.mark.parametrize(("changes", "code"), [
    ({"disk": "/dev/sda1"}, "invalid-disk"),
    ({"role": "primary"}, "invalid-role"),
    ({"primary_url": "https://main.example:8443"}, "invalid-primary-url"),
    ({"role": "secondary", "primary_url": "http://main:8443"}, "invalid-primary-url"),
    ({"hostname": "-bad"}, "invalid-hostname"),
    ({"servers_if": "ens99"}, "invalid-interface"),
    ({"servers_mac": "bad"}, "invalid-mac"),
    ({"servers_mac": "00:50:56:01:02:02"}, "mac-mismatch"),
    ({"servers_mode": "manual"}, "invalid-network-mode"),
    ({"servers_addr": "10.0.0.8"}, "invalid-network"),
    ({"servers_mode": "static", "servers_addr": "999.0.0.8",
      "servers_mask": "255.255.255.0", "servers_gw": "10.0.0.1",
      "servers_dns": "10.0.0.2"}, "invalid-address"),
    ({"servers_mode": "static", "servers_addr": "10.0.0.8",
      "servers_mask": "bad", "servers_gw": "10.0.0.1",
      "servers_dns": "10.0.0.2"}, "invalid-netmask"),
    ({"servers_mode": "static", "servers_addr": "10.0.0.8",
      "servers_mask": "255.255.255.0", "servers_gw": "bad",
      "servers_dns": "10.0.0.2"}, "invalid-gateway"),
    ({"servers_mode": "static", "servers_addr": "10.0.0.8",
      "servers_mask": "255.255.255.0", "servers_gw": "10.0.0.1",
      "servers_dns": "10.0.0.2,bad"}, "invalid-dns"),
    ({"admin_pass": ""}, "invalid-admin-password"),
    ({"admin_user": "Admin"}, "invalid-admin-user"),
    ({"admin_user": "-x"}, "invalid-admin-user"),
])
@LINUX_ONLY
def test_bad_answers_are_named_before_any_disk_command(tmp_path, changes, code):
    box = _machine(tmp_path)
    calls = _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers", **changes), "--dry-run")
    assert got.returncode == 2 and f"[{code}]" in got.stderr
    assert not calls.exists(), calls.read_text(encoding="utf-8") if calls.exists() else ""


@LINUX_ONLY
def test_iso_device_is_refused_before_any_disk_command(tmp_path):
    box = _machine(tmp_path)
    box["mounts"].write_text("/dev/sda1 /cdrom iso9660 ro 0 0\n", newline="\n")
    calls = _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers"), "--dry-run")
    assert got.returncode == 2 and "[iso-target]" in got.stderr
    assert not calls.exists()


@LINUX_ONLY
def test_inventory_has_the_documented_line_from_sysfs_and_blkid(tmp_path):
    box = _machine(tmp_path)
    disk = box["root"] / "sys" / "block" / "sda"
    for part in ("sda1", "sda2"):
        (disk / part).mkdir()
        (disk / part / "partition").write_text("1\n", newline="\n")
    _command_shims(tmp_path, box["env"])
    got = _run(box, None, "--inventory")
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == (
        "disk=/dev/sda|model=VMware Virtual disk|size=64424509440|bus=sata|"
        "removable=0|has=gpt:2 partitions (vfat, ext4)|iso=0"
    )


def _normal(text: str, box) -> list[str]:
    for key in ("target", "cdrom", "dev"):
        text = text.replace(str(box[key]), f"<{key}>")
    return [line for line in text.splitlines() if line.startswith("+")]


@LINUX_ONLY
def test_dry_run_prints_the_exact_command_sequence_and_redacts_secrets(tmp_path):
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers"), "--dry-run")
    assert got.returncode == 0, got.stderr
    lines = _normal(got.stdout, box)
    assert lines[:11] == [
        "+ sgdisk --zap-all <dev>/sda",
        "+ sgdisk --new=1:1MiB:+512MiB --typecode=1:EF00 --change-name=1:EFI --new=3:0:+1MiB --typecode=3:EF02 --change-name=3:bios_grub --new=2:0:0 --typecode=2:8300 --change-name=2:root <dev>/sda",
        "+ blockdev --rereadpt <dev>/sda",
        "+ mkfs.vfat -F 32 -n EFI <dev>/sda1",
        "+ mkfs.ext4 -F -L root <dev>/sda2",
        "+ mkdir -p <target>",
        "+ mount <dev>/sda2 <target>",
        "+ mkdir -p <target>/boot/efi",
        "+ mount <dev>/sda1 <target>/boot/efi",
        "+ debootstrap --arch amd64 --no-check-gpg trixie <target> file://<cdrom>",
        "+ mkdir -p <target>/dev <target>/proc <target>/sys <target>/cdrom",
    ]
    assert [line.split()[1] for line in lines] == [
        "sgdisk", "sgdisk", "blockdev", "mkfs.vfat", "mkfs.ext4",
        "mkdir", "mount", "mkdir", "mount", "debootstrap", "mkdir",
        "mount", "mount", "mount", "mount", "write-target", "rm",
        "chroot", "chroot", "cp", "cp", "chmod", "cp", "chroot",
        "mkdir", "mkdir", "cp", "cp", "verify-package-pool", "write-target",
        "write-target", "write-target", "write-target", "cp", "printf",
        "chroot", "chroot", "chroot", "chroot", "blkid", "blkid",
        "write-target", "write-target", "write-target", "write-target",
        "write-target-secret", "umount", "umount", "umount", "umount",
        "umount", "umount", "sync",
    ]
    assert "+ chroot <target> apt-get install -y --no-install-recommends busybox-static linux-image-amd64 grub-efi-amd64-signed shim-signed grub-pc-bin" in lines
    # --removable and --force-extra-removable are mutually exclusive in Debian's grub-install (ESXi, 21/09)
    assert "+ chroot <target> grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=debian --force-extra-removable" in lines
    assert "--removable --force-extra-removable" not in got.stdout
    assert "+ chroot <target> grub-install --target=i386-pc /dev/sda" in lines
    assert lines[-7:] == [
        "+ umount <target>/cdrom", "+ umount <target>/sys", "+ umount <target>/proc",
        "+ umount <target>/dev", "+ umount <target>/boot/efi", "+ umount <target>", "+ sync",
    ]
    assert "correct horse" not in got.stdout + got.stderr
    assert "reboot" not in got.stdout
    for state in ("partitioning", "bootstrap", "packages", "bootloader", "finishing", "done"):
        assert f"state={state} " in got.stderr
    assert box["state"].read_text(encoding="utf-8").splitlines()[:3] == [
        "state=done", "pct=100", "title=Installation complete"]


@LINUX_ONLY
def test_failed_debootstrap_sets_failed_state_and_never_reaches_grub(tmp_path):
    box = _machine(tmp_path)
    calls = _command_shims(tmp_path, box["env"], debootstrap_rc=7)
    got = _run(box, _answers(tmp_path / "answers"))
    assert got.returncode == 2 and "[bootstrap-failed]" in got.stderr
    state = box["state"].read_text(encoding="utf-8")
    assert "state=failed" in state and "error=bootstrap-failed: debootstrap exited 7" in state
    called = calls.read_text(encoding="utf-8")
    assert "debootstrap --arch amd64" in called
    assert "grub-install" not in called


@LINUX_ONLY
def test_a_source_tree_without_git_fails_unless_the_manifest_says_lab_iso(tmp_path):
    """The update button works on .git (#1185): a public-clone ISO always has
    it, a lab ISO (build-iso --source) declares source_git=false and passes
    with the gap named in the progress log."""
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    for name in ("chown", "sync", "chmod"):  # the real run past debootstrap, without root
        _shim(tmp_path / "bin" / name, "exit 0\n")
    shutil.rmtree(Path(box["env"]["CDROM"]) / "imagectl-src" / ".git")
    (box["target"] / "opt").mkdir()  # debootstrap (shimmed here) would have made it
    got = _run(box, _answers(tmp_path / "answers"))
    assert got.returncode == 2 and "[source-git-missing]" in got.stderr
    shutil.rmtree(box["target"] / "opt"); (box["target"] / "opt").mkdir()
    (Path(box["env"]["CDROM"]) / "imagectl-iso.json").write_text(
        '{"tag":"test","source_git":false}\n', newline="\n")
    got = _run(box, _answers(tmp_path / "answers"))
    # past the check: the run goes on to the firstboot copy (which needs a
    # populated target this shimmed box does not have)
    assert "[source-git-missing]" not in got.stderr
    assert "the update button will not work" in got.stderr
    assert "[firstboot-copy]" in got.stderr


@LINUX_ONLY
def test_the_target_tree_is_world_readable_whatever_umask_the_engine_inherits(tmp_path):
    """The console process runs under umask 077 and the engine inherited it:
    apt's sandbox user could not read /var/lib/imagectl/apt-repo on the
    installed server (ESXi, 21/09). Secrets keep their explicit 0600."""
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    for name in ("chown", "sync", "chmod"):
        _shim(tmp_path / "bin" / name, "exit 0\n")
    (box["target"] / "opt").mkdir()
    engine = tmp_path / "engine.sh"
    engine.write_text(f"#!/bin/sh\numask 077\nexec {SH} {ENGINE} \"$@\"\n", newline="\n")
    engine.chmod(0o755)
    _run(box, _answers(tmp_path / "answers"), engine=engine)  # stops at firstboot-copy on this box
    # the directories the engine itself creates before that point stand in
    # for apt-repo (created later by the same mkdir under the same umask)
    for path in (box["target"] / "cdrom", box["target"] / "boot" / "efi", box["target"] / "sys"):
        assert path.is_dir(), path
        assert path.stat().st_mode & 0o055 == 0o055, f"{path} is not world-readable"
    answers = box["target"] / "etc" / "imagectl" / "answers"
    assert not answers.exists() or answers.stat().st_mode & 0o077 == 0


@LINUX_ONLY
def test_cleanup_unmounts_what_debootstrap_left_under_the_target(tmp_path):
    """debootstrap leaves its own proc mounted inside the target; the
    flag-tracked umount peeled only our bind and the root stayed busy
    (ESXi, 21/09). Leftovers under the target are unmounted by name."""
    box = _machine(tmp_path)
    calls = _command_shims(tmp_path, box["env"])
    target = box["target"]
    box["mounts"].write_text(
        f"/dev/sr0 /cdrom iso9660 ro 0 0\nproc {target}/proc proc rw 0 0\n", newline="\n")
    got = _run(box, _answers(tmp_path / "answers"))  # fails at the source copy; cleanup still runs
    assert f"installer: unmounted leftover {target}/proc" in got.stderr
    assert f"umount {target}/proc\n" in calls.read_text(encoding="utf-8")
    assert "unmount-failed" not in got.stderr


@LINUX_ONLY
def test_removing_the_iso_guard_makes_the_destructive_dry_run_reachable(tmp_path):
    """Negative control: one mutation, behavioural failure, not an import failure."""
    box = _machine(tmp_path)
    box["mounts"].write_text("/dev/sda1 /cdrom iso9660 ro 0 0\n", newline="\n")
    mutated = tmp_path / "installer"
    shutil.copytree(INSTALLER, mutated)
    answers_sh = mutated / "lib" / "answers.sh"
    source = answers_sh.read_text(encoding="utf-8")
    guard = '    disk_is_iso "$DISK_NAME" && fail iso-target "refusing the disk that contains /cdrom: $ANSWER_DISK"\n'
    assert source.count(guard) == 1
    answers_sh.write_text(source.replace(guard, ""), newline="\n")
    box["env"]["AGENT_LIB_DIR"] = str(REPO / "agent" / "lib")
    got = _run(box, _answers(tmp_path / "answers"), "--dry-run", engine=mutated / "imagectl-install")
    assert got.returncode == 0, got.stderr
    assert "+ sgdisk --zap-all" in got.stdout


@pytest.mark.parametrize("path", sizelimit.guarded_shell_files(REPO), ids=lambda p: p.name)
def test_initramfs_shell_files_stay_under_300_lines(path):
    sizelimit.assert_within_limit(path)


@pytest.mark.parametrize("path", [ENGINE, *(INSTALLER / "lib").glob("*.sh")], ids=lambda p: p.name)
def test_installer_is_posix_sh(path):
    got = subprocess.run([SH, "-n", str(path)], text=True, capture_output=True,
                         stdin=subprocess.DEVNULL)
    assert got.returncode == 0, got.stderr
    text = path.read_text(encoding="utf-8")
    for bashism in ("[[", "]]", "declare -a", "declare -A", "${BASH_SOURCE", "<(" , "<<<"):
        assert bashism not in text, f"{path.name}: bashism {bashism!r}"


def test_installer_packages_and_binaries_are_conditional_and_on_the_iso():
    build = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    packages = (REPO / "tools" / "iso" / "packages.txt").read_text(encoding="utf-8")
    assert "--installer)      WITH_INSTALLER=1" in build
    assert "TOOL_BINS+=(debootstrap dpkg-deb mkfs.vfat chroot mkfs.ext4 eject perl dash tar)" in build
    assert "[dash]=dash" in build and "[tar]=tar" in build
    # busybox applets must not shadow the packed tools (ESXi, 21/09)
    assert 'rm "$ROOT/bin/$_shadow"' in build
    assert "[debootstrap]=debootstrap" in build and "[mkfs.vfat]=dosfstools" in build
    assert "debootstrap" in packages and "dosfstools" in packages and "dpkg" in packages
