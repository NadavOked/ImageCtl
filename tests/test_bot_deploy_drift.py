"""deploy-drift bot tests; runnable with unittest, no pytest children, no model, no gh, no ssh.

The bot reads the deployed ref from DEPLOYED_REF (or, live, the gh deployments
API) and compares it to a main ref with plain git. These tests drive it through
a throwaway git repo and DEPLOYED_REF/MAIN_REF, so neither gh nor ollama nor ssh
is ever touched.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / 'tools/agents/bot-deploy-drift.sh'


def _git(*argv, **kwargs):
    # DEVNULL stdin keeps the child alive under pytest capture on Windows (#14).
    return subprocess.run(argv, check=True, stdin=subprocess.DEVNULL, **kwargs)


@unittest.skipUnless(shutil.which('bash'), 'bash is required for the deploy-drift bot run')
class DeployDriftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inbox = self.root / 'inbox'
        _git('git', 'init', '-q', '-b', 'main', str(self.root))
        _git('git', '-C', str(self.root), 'config', 'user.email', 't@t')
        _git('git', '-C', str(self.root), 'config', 'user.name', 't')
        # A = deployable base; B = non-contract change; C = contract change.
        self.a = self._commit('server/api.py', 'v = 1\n', 'base')
        self.a2 = self._commit('docs/notes.md', 'notes\n', 'more base')  # A..A2 no contract
        self.b = self._commit('docs/notes.md', 'notes edited\n', 'doc only')
        self.c = self._commit('server/api.py', 'v = 2\n', 'server change')

    def _commit(self, name, content, msg):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8', newline='\n')
        _git('git', '-C', str(self.root), 'add', '--', name)
        _git('git', '-C', str(self.root), 'commit', '-qm', msg)
        return subprocess.run(
            ['git', '-C', str(self.root), 'rev-parse', 'HEAD'],
            check=True, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        ).stdout.strip()

    def run_bot(self, deployed=None, main_ref='main', drop=()):
        env = {k: v for k, v in os.environ.items()
               if k not in ('DEPLOYED_REF', 'IMAGECTL_REPO', 'MAIN_REF', 'DEPLOY_ENV')}
        env['INBOX_SKIP_MODEL'] = '1'
        env['INBOX_DIR'] = str(self.inbox)
        env['MAIN_REF'] = main_ref
        if deployed is not None:
            env['DEPLOYED_REF'] = deployed
        for k in drop:
            env.pop(k, None)
        result = subprocess.run(['bash', str(BOT)], cwd=str(self.root), env=env,
                                capture_output=True, text=True, stdin=subprocess.DEVNULL)
        report = next(self.inbox.glob('*-deploy-drift.md'), None)
        body = report.read_text(encoding='utf-8') if report else ''
        return result, report, body

    # --- the bot fires and names the source when there is real drift ----------

    def test_behind_with_contract_change_reports_two_candidates(self):
        result, report, body = self.run_bot(deployed=self.a, main_ref='main')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(body.count('## candidate'), 2, body)
        self.assertIn('commits behind main', body)
        # A..C is three commits; server/api.py is the contract file that moved.
        self.assertIn('is 3 commits behind', body)
        self.assertIn('server/api.py', body)
        self.assertIn('git rev-list --count', body)        # verbatim names its source
        self.assertTrue(body.rstrip().endswith('## nothing else found'))
        self.assertFalse(report.name.endswith('.TRUNCATED'))

    def test_behind_without_contract_change_reports_only_the_count(self):
        # A..A2 advances by one commit that touches docs/ only, no contract path.
        result, report, body = self.run_bot(deployed=self.a, main_ref=self.a2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(body.count('## candidate'), 1, body)
        self.assertIn('is 1 commits behind', body)
        self.assertNotIn('contract', body.lower())
        self.assertTrue(body.rstrip().endswith('## nothing else found'))

    # --- negative control: no drift must produce no candidates ----------------

    def test_up_to_date_reports_nothing(self):
        result, report, body = self.run_bot(deployed=self.c, main_ref='main')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('## candidate', body)
        self.assertNotIn('could not check', body)
        self.assertTrue(body.rstrip().endswith('## nothing else found'))

    # --- principle 5: could-not-check is not a clean run ----------------------

    def test_no_deployed_ref_and_no_repo_is_could_not_check(self):
        result, report, body = self.run_bot(deployed=None, main_ref='main')
        self.assertEqual(result.returncode, 2, result.stderr)      # did not run
        self.assertNotIn('## candidate', body)                     # nothing flagged...
        self.assertIn('could not check', body)                     # ...but NOT clean
        self.assertTrue(body.rstrip().endswith('## nothing else found'))

    def test_unknown_deployed_ref_is_could_not_check_not_drift(self):
        result, report, body = self.run_bot(deployed='deadbeefdeadbeef', main_ref='main')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn('## candidate', body)
        self.assertIn('could not check', body)
        self.assertIn('not found locally', body)

    def test_missing_main_ref_is_could_not_check(self):
        result, report, body = self.run_bot(deployed=self.a, main_ref='origin/main')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('could not check', body)
        self.assertIn("main ref 'origin/main' not found", body)


if __name__ == '__main__':
    unittest.main(verbosity=2)
