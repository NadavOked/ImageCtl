"""#1464 -- the one-pass module closure packs exactly what the old loop packed.

The old closure in ``tools/build_initramfs.sh`` ran ``find`` over the whole
kernel module tree, plus three subshells per file, for every soft-dependency
name of every staged module in every round. The rewrite lists the tree once and
resolves each round in one awk. The image is reproducible (#1125), so "faster"
is only acceptable with the *same bytes*: here the old block (kept verbatim in
``tests/fixtures/initramfs_closure_v0743.sh``) and the current block run on
identical fake module trees, and the packed files, the round count and the exit
status must match.

The trees cover what the old loop did on purpose: the prefix match of a soft
name (``crc32c`` -> ``crc32c_generic``, ``crc32c-intel``), the alias table
(``blake2b-256`` -> ``blake2b_generic``, #1169), built-in modules, a hard
dependency that is listed but absent (skipped, as before), chains that need
several rounds, and a soft dependency that cannot be resolved (named failure).
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_initramfs_modules import KVER, closure_snippet

pytestmark = requires_native("sh", why="both closure blocks run in sh")

REFERENCE = Path(__file__).resolve().parent / "fixtures" / "initramfs_closure_v0743.sh"


def _reference() -> str:
    return "\n".join(l for l in REFERENCE.read_text(encoding="utf-8").split("\n")
                     if not l.startswith("# "))


def _write_tree(base: Path, tree: dict) -> tuple[Path, Path]:
    src, root = base / "src", base / "root"
    for rel in tree["files"]:
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(rel.encode())
    for name in ("modules.dep", "modules.softdep", "modules.alias", "modules.builtin"):
        if tree.get(name) is not None:
            (src / name).write_text(tree[name], encoding="utf-8", newline="\n")
    mods = root / "lib" / "modules" / KVER
    mods.mkdir(parents=True)
    for rel in tree["staged"]:
        (mods / rel).parent.mkdir(parents=True, exist_ok=True)
        (mods / rel).write_bytes(rel.encode())
    return src, root


def _run(base: Path, tree: dict, block: str):
    src, root = _write_tree(base, tree)
    script = (f"ROOT={root.as_posix()!r}\nKVER={KVER!r}\n"
              f"MODSRC={src.as_posix()!r}\n" + block)
    done = subprocess.run(["sh", "-c", script], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=600)
    packed = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    return done, packed


def _same(tmp_path: Path, tree: dict):
    old, old_packed = _run(tmp_path / "old", tree, _reference())
    new, new_packed = _run(tmp_path / "new", tree, closure_snippet())
    assert new.returncode == old.returncode, (old.stderr, new.stderr)
    if old.returncode == 0:
        assert new_packed == old_packed, set(new_packed) ^ set(old_packed)
        assert new.stdout == old.stdout, (old.stdout, new.stdout)   # "... modules after N rounds"
    else:
        # The old loop stopped at the first unresolved name; the new one may name them all.
        # (the old one also let awk complain about a missing modules.builtin first)
        first = next(l for l in old.stderr.split("\n") if l.startswith("soft dependency "))
        first = first.replace((tmp_path / "old").as_posix(), "<base>")
        named = new.stderr.replace((tmp_path / "new").as_posix(), "<base>").split("\n")
        assert first in named, (old.stderr, new.stderr)
    return old, new_packed


KITCHEN_SINK = {
    "files": [
        "kernel/lib/libcrc32c.ko.xz",
        "kernel/crypto/crc32c_generic.ko.xz",
        "kernel/arch/x86/crypto/crc32c-intel.ko.zst",
        "kernel/crypto/crc32cx.ko.xz",                 # not "crc32c" nor "crc32c_*": stays out
        "kernel/fs/btrfs/btrfs.ko.xz",
        "kernel/crypto/blake2b_generic.ko.xz",          # reached through the alias table only
        "kernel/fs/xfs/xfs.ko",
        "kernel/net/netfilter/nf_conntrack.ko.xz",
        "kernel/net/ipv4/netfilter/nf_defrag_ipv4.ko.xz",
        "updates/nf-defrag-ipv4.ko.xz",                 # same name in a second tree: both packed
        "kernel/drivers/a/chain-a.ko.xz",
        "kernel/drivers/b/chain_b.ko.xz",
        "kernel/drivers/c/chain-c.ko.xz",
        "kernel/drivers/d/chain_d.ko.xz",
        "kernel/drivers/e/chain-e.ko.xz",
        "kernel/drivers/z/unrelated.ko.xz",
    ],
    "staged": ["kernel/lib/libcrc32c.ko.xz", "kernel/fs/btrfs/btrfs.ko.xz",
               "kernel/fs/xfs/xfs.ko", "kernel/net/netfilter/nf_conntrack.ko.xz",
               "kernel/drivers/a/chain-a.ko.xz"],
    "modules.dep": (
        "kernel/fs/btrfs/btrfs.ko.xz: kernel/lib/libcrc32c.ko.xz kernel/lib/gone.ko.xz\n"
        "kernel/drivers/a/chain-a.ko.xz: kernel/drivers/b/chain_b.ko.xz\n"
        "kernel/drivers/c/chain-c.ko.xz: kernel/drivers/d/chain_d.ko.xz\n"
        "kernel/lib/libcrc32c.ko.xz:\n"
    ),
    "modules.softdep": (
        "# comment line\n"
        "softdep libcrc32c pre: crc32c\n"
        "softdep btrfs pre: blake2b-256 xxhash64\n"
        "softdep xfs pre: crc32c post: sha256\n"
        "softdep nf-conntrack pre: nf-defrag-ipv4\n"
        "softdep chain_b post: chain-c\n"
        "softdep chain-d pre: chain_e foo-bar\n"
    ),
    "modules.alias": (
        "alias crypto-blake2b-256 blake2b_generic\n"
        "alias blake2b-256 blake2b_generic\n"
        "alias foo-bar foo_impl\n"
    ),
    "modules.builtin": (
        "kernel/lib/xxhash64.ko\n"
        "kernel/lib/crypto/sha256.ko\n"
        "kernel/drivers/foo/foo-impl.ko\n"
    ),
}


def test_the_kitchen_sink_tree_packs_the_same_files_in_the_same_rounds(tmp_path: Path):
    old, packed = _same(tmp_path, KITCHEN_SINK)
    assert old.returncode == 0, old.stderr
    mods = f"lib/modules/{KVER}/"
    for rel in ("kernel/crypto/crc32c_generic.ko.xz", "kernel/arch/x86/crypto/crc32c-intel.ko.zst",
                "kernel/crypto/blake2b_generic.ko.xz", "updates/nf-defrag-ipv4.ko.xz",
                "kernel/net/ipv4/netfilter/nf_defrag_ipv4.ko.xz", "kernel/drivers/e/chain-e.ko.xz"):
        assert mods + rel in packed, rel
    assert mods + "kernel/crypto/crc32cx.ko.xz" not in packed
    assert mods + "kernel/drivers/z/unrelated.ko.xz" not in packed
    assert "after 5 rounds" in old.stdout, old.stdout   # a -> b -> c -> d -> e, one hop per round


@pytest.mark.parametrize("broken", ["unknown-name", "no-builtin-file"])
def test_an_unresolved_soft_dependency_fails_by_name_in_both(tmp_path: Path, broken: str):
    tree = dict(KITCHEN_SINK)
    if broken == "unknown-name":
        tree["modules.softdep"] += "softdep xfs post: nosuch-thing\n"
    else:
        tree["modules.builtin"] = None    # xxhash64/sha256 cannot be confirmed built-in
    old, _ = _same(tmp_path, tree)
    assert old.returncode != 0
    assert "soft dependency" in old.stderr and "is missing under" in old.stderr


def _random_tree(seed: int, *, fail: bool) -> dict:
    rnd = random.Random(seed)
    stems = ["alpha", "beta", "gamma", "delta", "eps", "zeta", "eta", "theta", "iota",
             "kappa", "lam", "mu", "nu", "xi", "omi", "pi", "rho", "sig"]
    names = []
    for i in range(12):
        base = stems[i]
        names.append(base if rnd.random() < 0.5 else base + rnd.choice(["_core", "-hcd", "_generic", "-intel"]))
    dirs = ["kernel/drivers/net", "kernel/fs", "kernel/crypto", "kernel/lib", "updates"]
    exts = [".ko", ".ko.xz", ".ko.zst"]
    files = {n: f"{rnd.choice(dirs)}/{n}{rnd.choice(exts)}" for n in names}
    dep_lines = []
    for i, n in enumerate(names):
        later = names[i + 1:]
        deps = [files[d] for d in rnd.sample(later, min(len(later), rnd.randint(0, 2)))]
        if rnd.random() < 0.1:
            deps.append("kernel/lib/absent-dep.ko.xz")
        dep_lines.append(f"{files[n]}:" + "".join(" " + d for d in deps))
    builtin = [f"kernel/lib/{s}-bi.ko" for s in ("crc", "sha", "aes")]
    alias = ["alias crypto-cap-one " + names[-1].replace("_", "-"),
             "alias cap-two crc_bi"]
    soft_lines = []
    for n in names:
        if rnd.random() < 0.35:
            pool = [
                rnd.choice(names).split("_")[0].split("-")[0],      # prefix of some module
                rnd.choice(names).replace("_", "-"),                # exact, dash spelling
                "cap-one", "cap_two", "sha-bi",                     # alias / alias->builtin / builtin
            ]
            pre = rnd.sample(pool, rnd.randint(1, 2))
            post = rnd.sample(pool, rnd.randint(0, 1))
            field = n.replace("_", "-") if rnd.random() < 0.5 else n
            soft_lines.append(f"softdep {field} pre: {' '.join(pre)}"
                              + (f" post: {' '.join(post)}" if post else ""))
    if fail:
        soft_lines.append(f"softdep {names[len(names) // 2]} pre: never-there")
    staged = rnd.sample(names, 3)
    if fail:
        staged.append(names[len(names) // 2])
    return {
        "files": sorted(files.values()),
        "staged": sorted({files[n] for n in staged}),
        "modules.dep": "\n".join(dep_lines) + "\n",
        "modules.softdep": "\n".join(soft_lines) + "\n",
        "modules.alias": "\n".join(alias) + "\n",
        "modules.builtin": "\n".join(builtin) + "\n",
    }


@pytest.mark.parametrize("seed", range(4))
def test_random_trees_pack_the_same_files(tmp_path: Path, seed: int):
    _same(tmp_path, _random_tree(seed, fail=False))


@pytest.mark.parametrize("seed", [100, 101])
def test_random_trees_with_an_unresolved_name_fail_the_same(tmp_path: Path, seed: int):
    old, _ = _same(tmp_path, _random_tree(seed, fail=True))
    assert old.returncode != 0
