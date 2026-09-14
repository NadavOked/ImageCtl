"""Two-way negative control for the orphan-worktrees bot (#617).

The bot is POSIX-ish bash over real `git` and `gh` reads, so the test drives
the script itself against a throwaway git repo with a stubbed `gh`. It proves
the bot FIRES on orphans and is SILENT on a clean tree -- and that a `gh`
outage is surfaced, never folded into "clean" (principle 5)."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / 'tools/agents/bot-orphan-worktrees.sh'
BASH = shutil.which('bash') or shutil.which('bash.exe')

RECENT = '2026-09-01T00:00:00 +0000'   # ~9 days before NOW
OLD = '2020-01-01T00:00:00 +0000'      # ~2000 days before NOW
NOW = '1757462400'                     # 2026-09-10, fixed for determinism


@unittest.skipUnless(BASH, 'bash is required to run the bot script')
class OrphanWorktreesTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.repo.mkdir()
        self.inbox = Path(self.temp.name) / 'inbox'

    def git(self, *args, date=None):
        env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t',
                   GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
        if date:
            env['GIT_AUTHOR_DATE'] = env['GIT_COMMITTER_DATE'] = date
        # stdin=DEVNULL: under pytest capture on Windows an inherited stdin
        # handle is invalid and every git child dies in DuplicateHandle (#14).
        subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, env=env)

    def commit(self, message, date):
        self.git('commit', '--allow-empty', '-m', message, date=date)

    def gh_stub(self, *open_branches, fail=False):
        path = Path(self.temp.name) / 'gh'
        body = '#!/bin/sh\nexit 1\n' if fail else '#!/bin/sh\n' + ''.join(
            f'echo {b}\n' for b in open_branches)
        path.write_text(body, encoding='utf-8', newline='\n')
        path.chmod(0o755)
        return str(path)

    def run_bot(self, gh):
        env = dict(os.environ, INBOX_DIR=str(self.inbox), INBOX_SKIP_MODEL='1',
                   ORPHAN_GH=gh, ORPHAN_NOW=NOW, INBOX_STAMP='2026-09-10-000000')
        proc = subprocess.run([BASH, str(BOT)], cwd=str(self.repo), env=env,
                              stdin=subprocess.DEVNULL, capture_output=True,
                              encoding='utf-8', errors='replace')
        reports = sorted(self.inbox.glob('*')) if self.inbox.exists() else []
        return proc, reports

    def base_repo(self):
        """Main at a recent commit, with origin/main tracking it locally."""
        self.git('init', '-q', '-b', 'main')
        self.commit('root', RECENT)
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')

    def test_fires_on_orphans_and_is_silent_on_the_clean_parts(self):
        self.base_repo()
        # A merged branch checked out in a worktree -> flagged.
        self.git('branch', 'feature-merged')
        self.git('worktree', 'add', '-q', str(self.repo.parent / 'wt_merged'), 'feature-merged')
        # A worktree whose backing directory is deleted -> prunable -> flagged.
        self.git('worktree', 'add', '-q', '-b', 'tmpbr', str(self.repo.parent / 'wt_prune'))
        shutil.rmtree(self.repo.parent / 'wt_prune')
        # An old branch with no open PR -> flagged; the same age WITH a PR -> not.
        self.git('branch', 'stale-nopr')
        self.git('checkout', '-q', 'stale-nopr')
        self.commit('old work', OLD)
        self.git('branch', 'active-pr')        # same old commit, but has a PR
        self.git('checkout', '-q', 'main')

        proc, reports = self.run_bot(self.gh_stub('active-pr'))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(reports), 1, reports)
        report = reports[0].read_text(encoding='utf-8')
        self.assertFalse(reports[0].name.endswith('.TRUNCATED'))
        self.assertEqual(report.count('## candidate'), 3, report)
        self.assertIn("already merged into origin/main", report)
        self.assertIn('prunable', report)
        self.assertIn("branch 'stale-nopr' has no open PR", report)
        # The guarded branch and the non-worktree merged branch stay silent.
        self.assertNotIn('active-pr', report)
        self.assertIn('## nothing else found', report)

    def test_clean_tree_writes_nothing_else_found_and_no_candidates(self):
        self.base_repo()
        self.git('branch', 'active-pr')
        proc, reports = self.run_bot(self.gh_stub('active-pr'))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(reports), 1, reports)
        report = reports[0].read_text(encoding='utf-8')
        self.assertNotIn('## candidate', report)
        self.assertIn('## nothing else found', report)

    def test_gh_outage_is_surfaced_not_folded_into_clean(self):
        self.base_repo()
        proc, reports = self.run_bot(self.gh_stub(fail=True))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(len(reports), 1, reports)
        self.assertTrue(reports[0].name.endswith('.TRUNCATED'), reports[0].name)
        report = reports[0].read_text(encoding='utf-8')
        self.assertIn('could not check:', report)
        self.assertIn('gh pr list failed', report)
        self.assertNotIn('nothing else found', report)
        self.assertIn('could not check:', proc.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
