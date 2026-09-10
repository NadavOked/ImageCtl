"""interface-drift bot tests; runnable with unittest, no pytest children, no model."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/agents'))
import bot_interface_drift as drift


def _git(*argv, **kwargs):
    # DEVNULL stdin keeps the child alive under pytest capture on Windows (#14).
    return subprocess.run(argv, check=True, stdin=subprocess.DEVNULL, **kwargs)


# A tiny contract: fields as fenced JSON keys, inline quoted, and a backtick.
INTERFACES = '''# interfaces

```json
{ "schema": 1, "mac": "b4", "role": "classroom",
  "session": { "state": "open", "image_id": "img_1" } }
```

Login body: `{"username","password"}`.
The round exposes `ready_drives`.
'''

# Endpoint emits one documented field and one drifted one; reads a documented
# body field and a drifted one. manifest[...] = ... is a write, not a wire read,
# and json.dumps to a column is not a component boundary -- neither may leak.
SERVER = '''from fastapi.responses import JSONResponse
import json

async def resolve(request):
    body = await request.json()
    mac = body.get("mac")
    flag = body.get("undoc_body")
    manifest = load()
    manifest["_dir"] = "/x"
    column = json.dumps({"internal_only": 1})
    return JSONResponse({"role": "classroom", "newfield": mac, "nested": {"drift_deep": flag}})

async def other(request):
    ids = (await request.json()).get("id_list")
    return JSONResponse(build_it())
'''

AGENT = '''#!/bin/sh
post_hello() {
    printf '{"mac":"%s","drift_key":"%s"}' "$1" "$2"
}
read_back() {
    _a=$(json_get "$RESP" ".session.state")
    _b=$(json_get "$RESP" ".mystery_field")
    _c=$(json_get_join "$RESP" ".ready_drives")
}
'''


class InterfaceDriftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _git('git', 'init', '-q', str(self.root))
        _git('git', '-C', str(self.root), 'config', 'user.email', 't@t')
        _git('git', '-C', str(self.root), 'config', 'user.name', 't')

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8', newline='\n')
        _git('git', '-C', str(self.root), 'add', '--', name)
        return path

    def fixture(self):
        self.write('docs/interfaces.md', INTERFACES)
        self.write('server/api.py', SERVER)
        self.write('agent/lib/flow.sh', AGENT)

    def fields(self, since=None):
        candidates, coulds = drift.scan(self.root, since)
        return {c[0] for c in candidates}, coulds

    def test_reports_undocumented_wire_fields_both_sides(self):
        self.fixture()
        found, coulds = self.fields()
        self.assertEqual(found, {'newfield', 'nested', 'drift_deep', 'undoc_body',
                                 'id_list', 'drift_key', 'mystery_field'})
        # JSONResponse(build_it()) is a real response whose fields we could not read.
        self.assertTrue(any('server/api.py' in c and 'JSONResponse' in c
                            and 'not a dict literal' in c for c in coulds), coulds)

    def test_documented_and_internal_fields_are_never_flagged(self):
        self.fixture()
        found, _ = self.fields()
        for clean in ('mac', 'role', 'state', 'session', 'image_id', 'schema',
                      'username', 'password', 'ready_drives',  # documented
                      '_dir', 'internal_only'):               # internal, not a boundary
            self.assertNotIn(clean, found)

    def test_negative_control_documenting_the_field_silences_it(self):
        self.fixture()
        before, _ = self.fields()
        self.assertIn('drift_key', before)
        self.assertIn('mystery_field', before)
        # Add the drifted fields to the contract; the drift must disappear.
        doc = self.root / 'docs/interfaces.md'
        doc.write_text(INTERFACES + '\nNow documented: `drift_key` `mystery_field` '
                       '`newfield` `nested` `drift_deep` `undoc_body` `id_list`\n',
                       encoding='utf-8', newline='\n')
        after, _ = self.fields()
        self.assertEqual(after, set())

    def test_missing_contract_compares_nothing_and_is_not_clean(self):
        self.fixture()
        (self.root / 'docs/interfaces.md').unlink()
        found, coulds = self.fields()
        self.assertEqual(found, set())                  # nothing flagged...
        self.assertTrue(coulds)                          # ...but it is NOT a clean run
        self.assertIn('docs/interfaces.md', coulds[0])

    def test_since_scopes_to_changed_files(self):
        self.fixture()
        _git('git', '-C', str(self.root), 'commit', '-qm', 'base')
        # Touch only the agent file; the server drift must not be re-reported.
        self.write('agent/lib/flow.sh', AGENT.replace('mystery_field', 'later_field'))
        found, _ = self.fields(since='HEAD')
        self.assertIn('later_field', found)
        self.assertIn('drift_key', found)
        self.assertNotIn('newfield', found)
        self.assertNotIn('undoc_body', found)

    def test_unparseable_server_file_is_unverified_not_clean(self):
        self.fixture()
        self.write('server/bad.py', 'def broken(:\n')
        _, coulds = self.fields()
        self.assertTrue(any('server/bad.py' in c and 'could not parse' in c for c in coulds), coulds)

    @unittest.skipUnless(shutil.which('bash'), 'bash is required for the end-to-end bot run')
    def test_end_to_end_writes_inbox_file_with_low_confidence_candidates(self):
        self.fixture()
        # The .sh resolves its helpers from its own repo root, so mirror them in.
        dst = self.root / 'tools/agents'
        dst.mkdir(parents=True, exist_ok=True)
        for name in ('bot-interface-drift.sh', 'bot-interface-drift.md', 'bot_interface_drift.py'):
            shutil.copy(ROOT / 'tools/agents' / name, dst / name)
        inbox = self.root / 'inbox'
        env = dict(os.environ, INBOX_SKIP_MODEL='1', INBOX_DIR=str(inbox))
        result = subprocess.run(['bash', str(dst / 'bot-interface-drift.sh')],
                                cwd=str(self.root), env=env, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = next(inbox.glob('*-interface-drift.md'))
        body = report.read_text(encoding='utf-8')
        self.assertIn('## candidate', body)
        self.assertIn('**confidence:** low', body)
        self.assertTrue(body.rstrip().endswith('## nothing else found'))
        self.assertFalse(report.name.endswith('.TRUNCATED'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
