"""Actual authenticated HTTP lifecycle and private-trash integration checks."""
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote

import vwb
from companion.projects import ProjectGrant
from companion.server import create_server

ORIGIN = 'https://sfmth.github.io'
REPO = Path(__file__).resolve().parents[1]


class CompanionFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        (self.root / 'src').mkdir()
        (self.root / 'test').mkdir()
        (self.root / 'src/top.sv').write_bytes(b'module top;\r\nendmodule\r\n')
        (self.root / 'src/empty').mkdir()
        self.start()

    def start(self):
        self.server = create_server(self.root, ORIGIN, editor_config={key: False for key in ('systemverilog', 'sv_refactor', 'vhdl', 'python')})
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.token = None
        status, data = self.request('POST', '/v1/pair', {'code': self.server.state.pairing_code}, token=False)
        self.assertEqual(status, 200, data)
        self.token = data['token']

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)

    def tearDown(self):
        self.stop(); self.directory.cleanup()

    def request(self, method, path, body=None, *, token=True, origin=ORIGIN, host=None):
        headers = {'Origin': origin}
        if token and self.token: headers['Authorization'] = 'Bearer ' + self.token
        if host: headers['Host'] = host
        raw = None
        if body is not None:
            headers['Content-Type'] = 'application/json'; raw = json.dumps(body).encode()
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        connection.request(method, path, body=raw, headers=headers)
        response = connection.getresponse(); content = response.read(); status = response.status
        connection.close()
        return status, json.loads(content) if content else None

    def entry(self, path):
        status, data = self.request('GET', '/v1/project/entry?path=' + quote(path, safe=''))
        self.assertEqual(status, 200, data)
        return data

    def test_every_endpoint_enforces_exact_session_origin_and_host(self):
        requests = [('GET', '/v1/project/entry?path=src/top.sv', None), ('GET', '/v1/project/trash', None)]
        requests += [('POST', '/v1/project/' + name, {}) for name in ('create', 'move', 'trash', 'restore')]
        for method, path, body in requests:
            for options in ({'token': False}, {'origin': 'https://invalid.example'}, {'host': 'invalid.example'}):
                with self.subTest(path=path, options=options):
                    self.assertIn(self.request(method, path, body, **options)[0], (401, 403))
        self.assertEqual((self.root / 'src/top.sv').read_bytes(), b'module top;\r\nendmodule\r\n')

    def test_actual_create_move_trash_restart_and_restore_preserve_bytes(self):
        self.assertEqual(self.request('POST', '/v1/project/create', {'path': 'src/new µ', 'kind': 'directory'})[0], 200)
        content = '\ufeff// unicode µ\r\nmodule added; endmodule\r\n'
        status, created = self.request('POST', '/v1/project/create', {'path': 'src/new µ/added.sv', 'kind': 'file', 'content': content})
        self.assertEqual(status, 200, created)
        before = self.entry('src/new µ')
        status, moved = self.request('POST', '/v1/project/move', {'path': before['path'], 'destination': 'src/moved µ', 'revision': before['revision']})
        self.assertEqual(status, 200, moved)
        self.assertEqual(moved['previous_path'], 'src/new µ')
        self.assertEqual((self.root / 'src/moved µ/added.sv').read_bytes(), content.encode())
        status, trashed = self.request('POST', '/v1/project/trash', {'path': moved['path'], 'revision': moved['revision']})
        self.assertEqual(status, 200, trashed)
        self.assertFalse((self.root / moved['path']).exists())
        self.stop(); self.start()
        status, inventory = self.request('GET', '/v1/project/trash')
        self.assertEqual(status, 200, inventory)
        self.assertEqual(inventory['entries'][0]['id'], trashed['id'])
        status, restored = self.request('POST', '/v1/project/restore', {'id': trashed['id']})
        self.assertEqual(status, 200, restored)
        self.assertEqual((self.root / 'src/moved µ/added.sv').read_bytes(), content.encode())
        self.assertEqual(self.request('GET', '/v1/project/trash')[1]['entries'], [])

    def test_stale_entry_clean_buffer_and_destination_conflicts_never_overwrite(self):
        item = self.entry('src/top.sv')
        (self.root / item['path']).write_text('external')
        for operation in ('move', 'trash'):
            body = {'path': item['path'], 'revision': item['revision']}
            if operation == 'move': body['destination'] = 'src/else.sv'
            self.assertEqual(self.request('POST', '/v1/project/' + operation, body)[0], 409)
        fresh = self.entry(item['path'])
        body = {'path': item['path'], 'revision': fresh['revision'], 'destination': 'src/else.sv', 'documents': [{'path': item['path'], 'revision': item['revision']}]}
        self.assertEqual(self.request('POST', '/v1/project/move', body)[0], 409)
        (self.root / 'src/else.sv').write_text('keep destination')
        body.pop('documents')
        self.assertEqual(self.request('POST', '/v1/project/move', body)[0], 409)
        self.assertEqual((self.root / 'src/else.sv').read_text(), 'keep destination')
        self.assertEqual((self.root / item['path']).read_text(), 'external')

    def test_restore_refuses_an_occupied_original_path(self):
        item = self.entry('src/top.sv')
        status, entry = self.request('POST', '/v1/project/trash', {'path': item['path'], 'revision': item['revision']})
        self.assertEqual(status, 200, entry)
        (self.root / item['path']).write_text('new work')
        self.assertEqual(self.request('POST', '/v1/project/restore', {'id': entry['id']})[0], 409)
        self.assertEqual((self.root / item['path']).read_text(), 'new work')
        self.assertTrue(self.request('GET', '/v1/project/trash')[1]['entries'][0]['restorable'])

    def test_private_trash_is_not_an_api_path_inventory_source_or_artifact(self):
        before_source = self.server.state.project.source_revision()
        item = self.entry('src/top.sv')
        status, entry = self.request('POST', '/v1/project/trash', {'path': item['path'], 'revision': item['revision']})
        self.assertEqual(status, 200, entry)
        after_source = self.server.state.project.source_revision()
        self.assertNotEqual(before_source, after_source)
        grant = ProjectGrant(self.root, src_dir='.', test_dir='test')
        digest = grant.source_revision()
        private = f'.vwb-trash/{entry["id"]}/payload'
        for endpoint in ('/v1/project/file?path=', '/v1/project/entry?path=', '/v1/artifacts/file?path='):
            self.assertEqual(self.request('GET', endpoint + quote(private, safe=''))[0], 403)
        self.assertEqual(self.request('PUT', '/v1/project/file', {'path': private, 'revision': item['revision'], 'content': 'leak'})[0], 403)
        self.assertEqual(self.request('POST', '/v1/project/create', {'path': '.vwb-trash/leak', 'kind': 'file'})[0], 403)
        self.assertEqual(self.request('POST', '/v1/project/create', {'path': 'src/.vwb-trash', 'kind': 'directory'})[0], 403)
        (self.root / private).write_bytes(b'private changed contents')
        self.assertEqual(grant.source_revision(), digest)
        for artifacts in (False, True):
            inventory = grant.inventory(artifacts=artifacts)
            self.assertFalse(any('.vwb-trash' in item['path'] for item in inventory['files'] + inventory['directories']))

    def test_empty_directories_and_protected_roots_are_exposed_safely(self):
        status, inventory = self.request('GET', '/v1/project/files')
        self.assertEqual(status, 200, inventory)
        self.assertIn({'path': 'src/empty'}, inventory['directories'])
        for path in ('.', 'src', 'test', '.vwb', '.vwb.json', '../escape', '.git/config'):
            with self.subTest(path=path):
                self.assertIn(self.request('POST', '/v1/project/trash', {'path': path, 'revision': '0' * 64})[0], (400, 403))
        self.assertEqual(self.request('POST', '/v1/project/create', {'path': 'src/x', 'kind': 'directory', 'argv': ['bad']})[0], 400)
        self.assertEqual(self.request('POST', '/v1/project/create', {'path': 'src/\ud800', 'kind': 'file'})[0], 400)

    def test_native_jobs_semantic_mutation_and_pending_recovery_block_lifecycle(self):
        body = {'path': 'src/new.sv', 'kind': 'file'}
        state = self.server.state
        state.editor_busy = True
        self.assertEqual(self.request('POST', '/v1/project/create', body)[0], 409)
        state.editor_busy = False
        state.jobs['controlled'] = {'status': 'running'}
        try: self.assertEqual(self.request('POST', '/v1/project/create', body)[0], 409)
        finally: state.jobs.clear()
        with patch.object(state.edits, 'status', return_value={'recovery_required': True}):
            self.assertEqual(self.request('POST', '/v1/project/create', body)[0], 409)
        self.assertFalse((self.root / body['path']).exists())

    def test_successful_path_change_invalidates_all_semantic_plan_ids(self):
        self.server.state.editor_plans['controlled'] = ('old', 'old')
        self.server.state.editor_undos['controlled'] = ('old', 'old')
        self.assertEqual(self.request('POST', '/v1/project/create', {'path': 'src/created', 'kind': 'directory'})[0], 200)
        self.assertEqual(self.server.state.editor_plans, {})
        self.assertEqual(self.server.state.editor_undos, {})

    def test_cli_discovery_never_revives_trashed_hdl_python_or_includes(self):
        (self.root / 'src/discard').mkdir()
        (self.root / 'src/discard/removed.sv').write_text('module removed; endmodule\n')
        (self.root / 'src/discard/test_top.py').write_text('import cocotb\n@cocotb.test()\nasync def test_top(dut): pass\n')
        (self.root / 'src/discard/secret.svh').write_text('// not an include directory\n')
        item = self.entry('src/discard')
        self.assertEqual(self.request('POST', '/v1/project/trash', {'path': item['path'], 'revision': item['revision']})[0], 200)
        self.assertEqual(vwb.find_hdl_files(self.root), [self.root / 'src/top.sv'])
        self.assertFalse(any('.vwb-trash' in path.parts for path in vwb.project_files(self.root)))
        result = subprocess.run([sys.executable, str(REPO / 'vwb.py'), '--root', str(self.root), '--src-dir', 'src', '--test-dir', 'test', 'list', '--json'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        catalog = json.loads(result.stdout)
        self.assertIn('top', {module['name'] for module in catalog['modules']})
        self.assertNotIn('removed', {module['name'] for module in catalog['modules']})
        self.assertNotIn('.vwb-trash', result.stdout)


if __name__ == '__main__':
    unittest.main()
