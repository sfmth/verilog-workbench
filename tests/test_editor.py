"""Editor protocol/boundary tests; fake servers are NOT semantic evidence."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

from companion.editor import EditorService, MAX_DOCUMENT_BYTES, _snippet
from companion.projects import ProjectError, ProjectGrant, revision

REPO = Path(__file__).resolve().parents[1]
FAKE = r'''
import json,sys,time
sys.path.insert(0,sys.argv[1])
from companion.lsp import read_message,encode_message
mode=sys.argv[2]; documents={}
def send(message):
    sys.stdout.buffer.write(encode_message(message));sys.stdout.buffer.flush()
def region(start=0,end=5):
    return {'start':{'line':0,'character':start},'end':{'line':0,'character':end}}
while True:
    message=read_message(sys.stdin.buffer); method=message.get('method'); params=message.get('params',{})
    if method=='exit':break
    if method=='textDocument/didOpen':
        document=params['textDocument'];documents[document['uri']]=document;continue
    if 'id' not in message:continue
    uri=params.get('textDocument',{}).get('uri')
    if method=='initialize':
        if mode=='slow':time.sleep(10)
        result={'capabilities': {'hoverProvider':True,'completionProvider':{},'definitionProvider':True,
            'referencesProvider':True,'renameProvider':{'prepareProvider':True},'documentSymbolProvider':mode=='collision'}}
        # An empty options object is a supported LSP provider.
        result['capabilities']['completionProvider']={}
    elif method=='textDocument/hover':
        doc=documents[uri]
        send({'jsonrpc':'2.0','method':'textDocument/publishDiagnostics','params':{'uri':uri,
            'version':None if mode=='unversioned' else doc['version'],'diagnostics':[{'range':region(0,0),'message':'<b>current</b>','severity':2}]}})
        result={'contents':{'kind':'markdown','value':'<script>bad()</script>'+doc['text']+' OPEN='+str(len(documents))}}
    elif method=='textDocument/completion':
        result={'isIncomplete':True,'items':[{'label':'value','insertTextFormat':2,
            'textEdit':{'range':region(0,0),'newText':'value(${1:x})$0'},
            'additionalTextEdits':[{'range':region(0,5),'newText':'safe'}]},
            {'label':'value_safe','insertTextFormat':2,'textEdit':{'range':region(0,5),'newText':'value_safe(${1:x})$0'}},
            {'label':'unsupported','insertTextFormat':2,'insertText':'${TM_FILENAME}'}]}
    elif method=='textDocument/definition':
        loc_uri=('file:///etc/passwd' if mode=='external' else uri)
        result=[{'uri':loc_uri,'range':region(1,5) if mode=='disagree' else region()}]
    elif method=='textDocument/references': result=[{'uri':uri,'range':region()}]
    elif method=='textDocument/prepareRename': result=None if mode=='reject' else region()
    elif method=='textDocument/documentSymbol': result=[{'name':'value','selectionRange':region(),'range':region()},
        {'name':'existing','selectionRange':region(6,7),'range':region(6,7)}]
    elif method=='textDocument/rename':
        if mode=='server-error':
            send({'jsonrpc':'2.0','id':message['id'],'error':{'code':-32602,'message':'semantic rename collision'}});continue
        result={'documentChanges':[{'textDocument':{'uri':uri,'version':documents[uri]['version']},
                'edits':[{'range':region(),'newText':params['newName']}]}]}
    elif method=='shutdown':result=None
    else:result=None
    send({'jsonrpc':'2.0','id':message['id'],'result':result})
'''


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='editor μ ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'src').mkdir()
        self.path = self.root / 'src/value.py'
        self.path.write_bytes(b'value = 1\r\n')
        self.project = ProjectGrant(self.root)

    def command(self, mode='normal'):
        return [sys.executable, '-u', '-c', FAKE, str(REPO), mode]

    def service(self, mode='normal', language='python', alternate=None):
        config = {key: None for key in ('systemverilog', 'sv_refactor', 'python', 'vhdl')}
        config[language] = {'command': self.command(mode)}
        if alternate:
            config['sv_refactor'] = {'command': self.command(alternate)}
        service = EditorService(self.project, config)
        self.addCleanup(service.close)
        return service

    def payload(self, operation='hover', content=None, path='src/value.py'):
        value = self.project.read(path)
        return {'operation': operation, 'path': path, 'position': {'line': 0, 'character': 2},
                'documents': [{'path': path, 'content': value['content'] if content is None else content,
                               'revision': value['revision'], 'version': 9}]}

    def assert_status(self, expected, function):
        with self.assertRaises(ProjectError) as error:
            function()
        self.assertEqual(error.exception.status, expected, str(error.exception))
        return str(error.exception)

    def test_missing_servers_are_capabilities_not_semantic_passes(self):
        with mock.patch('companion.editor.shutil.which', return_value=None):
            service = EditorService(self.project)
        capabilities = service.capabilities()
        self.assertFalse(capabilities['languages']['python']['available'])
        self.assertIn('unproved', capabilities['languages']['python']['semantic_status'])
        self.assert_status(503, lambda: service.query(self.payload()))

    def test_local_configuration_is_only_command_source(self):
        service = self.service()
        for field in ('command', 'configuration', 'uri'):
            self.assert_status(400, lambda: service.query({**self.payload(), field: ['bad']}))
        for config in ({'unknown': {}}, {'python': {'command': 'shell command'}},
                       {'python': {'command': ['x'], 'extra': True}}, {'python': {'configuration': []}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                EditorService(self.project, config)

    def test_dirty_crlf_and_utf16_document_snapshots_reach_server(self):
        service = self.service()
        (self.root / 'src/other.py').write_text('other = 1\n')
        result = service.query(self.payload(content='🦉dirty\r\n'))
        self.assertIn('🦉dirty\r\n', result['result']['text'])
        self.assertIn('OPEN=2', result['result']['text'])
        self.assertNotIn('<script>', result['result']['text'])
        self.assertEqual(result['version'], 9)
        self.assertEqual(result['diagnostics'][0]['items'][0]['message'], 'current')
        self.assertEqual(self.path.read_bytes(), b'value = 1\r\n')
        invalid = self.payload(content='🦉dirty\r\n')
        invalid['position']['character'] = 1
        self.assert_status(422, lambda: service.query(invalid))

    def test_new_unsaved_document_is_supported_for_queries(self):
        service = self.service()
        payload = self.payload()
        payload['path'] = payload['documents'][0]['path'] = 'src/new.py'
        payload['documents'][0].update(revision=None, content='new = 1')
        result = service.query(payload)
        self.assertIn('new = 1', result['result']['text'])
        self.assertFalse((self.root / 'src/new.py').exists())

    def test_document_revisions_versions_and_active_path_are_validated(self):
        service = self.service()
        payload = self.payload()
        self.path.write_text('external edit')
        self.assert_status(409, lambda: service.query(payload))
        for field, value in (('version', True), ('version', -1), ('revision', 'invalid'), ('content', '\ud800')):
            payload = self.payload();payload['documents'][0][field] = value
            self.assert_status(400, lambda: service.query(payload))
        payload = self.payload();payload['path'] = 'src/other.py'
        self.assert_status(400, lambda: service.query(payload))
        payload = self.payload();payload['documents'] *= 2
        self.assert_status(400, lambda: service.query(payload))

    def test_actual_utf8_byte_limits_and_document_count(self):
        service = self.service()
        self.assert_status(413, lambda: service.query(self.payload(content='🦉' * (524288 + 1))))
        payload = self.payload();payload['documents'] *= 65
        self.assert_status(400, lambda: service.query(payload))
        payload = self.payload();payload['documents'] = []
        for index in range(5):
            payload['documents'].append({'path': f'src/new{index}.py', 'content': 'x' * (2 * 1024 * 1024),
                                         'revision': None, 'version': 1})
        payload['path'] = 'src/new0.py'
        self.assert_status(413, lambda: service.query(payload))

    def test_paths_build_git_and_internal_symlinks_fail_closed(self):
        service = self.service()
        for path in ('../outside.py', '.git/file.py', '.vwb/file.py', '/tmp/out.py'):
            payload = self.payload();payload['path'] = path
            self.assert_status(403, lambda: service.query(payload))
        try:
            (self.root / 'src/link.py').symlink_to(self.path)
        except OSError:
            self.skipTest('symlinks unavailable')
        payload = self.payload();payload['path'] = 'src/link.py'
        self.assert_status(403, lambda: service.query(payload))

    def test_definitions_are_project_relative_and_external_links_rejected(self):
        result = self.service().query(self.payload('definition'))
        self.assertEqual(result['result']['locations'][0]['path'], 'src/value.py')
        self.assert_status(422, lambda: self.service('external').query(self.payload('definition')))

    def test_completion_snippets_are_plain_text_and_overlap_is_rejected(self):
        result = self.service().query(self.payload('completion'))['result']
        self.assertEqual([item['label'] for item in result['items']], ['value_safe'])
        self.assertEqual(result['items'][0]['insert_text'], 'value_safe(x)')
        self.assertEqual(result['items'][0]['insert_text_format'], 1)
        self.assertTrue(result['is_incomplete'])
        self.assertEqual(_snippet('foo(${1|a,b|}, ${2:two})$0'), 'foo(a, two)')

    def test_rename_is_clean_preview_and_preserves_crlf_without_writing(self):
        service = self.service()
        payload = {**self.payload('rename'), 'new_name': 'renamed'}
        result = service.query(payload)['result']
        self.assertEqual(result['files'], [{'path': 'src/value.py', 'content': 'renamed = 1\r\n',
                         'revision': revision(b'value = 1\r\n'), 'edit_count': 1}])
        self.assertIn('no general', result['collision_check'])
        self.assertEqual(self.path.read_bytes(), b'value = 1\r\n')
        payload['documents'][0]['content'] = 'dirty = 1'
        self.assert_status(409, lambda: service.query(payload))

    def test_rename_prepare_rejection_identifiers_and_semantic_errors_propagate(self):
        payload = {**self.payload('rename'), 'new_name': 'renamed'}
        self.assert_status(422, lambda: self.service('reject').query(payload))
        self.assertIn('semantic rename collision', self.assert_status(422, lambda: self.service('server-error').query(payload)))
        for name in ('not valid', 'class', '', 'x.y', 42):
            self.assert_status(400, lambda: self.service().query({**payload, 'new_name': name}))

    def test_provable_same_scope_collision_is_rejected(self):
        payload = {**self.payload('rename'), 'new_name': 'existing'}
        self.assertIn('same scope', self.assert_status(422, lambda: self.service('collision').query(payload)))

    def test_systemverilog_slang_alone_never_renames(self):
        (self.root / 'src/value.sv').write_bytes(b'value = 1\n')
        payload = {**self.payload('rename', path='src/value.sv'), 'new_name': 'renamed'}
        service = self.service(language='systemverilog')
        self.assertFalse(service.capabilities()['languages']['systemverilog']['operations']['rename']['available'])
        self.assert_status(503, lambda: service.query(payload))
        good = self.service(language='systemverilog', alternate='normal')
        self.assertEqual(good.query(payload)['result']['files'][0]['content'], 'renamed = 1\n')
        bad = self.service(language='systemverilog', alternate='disagree')
        self.assertIn('disagree', self.assert_status(422, lambda: bad.query(payload)))

    def test_project_mutation_during_query_is_rejected(self):
        service = self.service()
        original = service._complete
        def mutate(*args):
            self.path.write_text('external edit')
            return original(*args)
        with mock.patch.object(service, '_complete', side_effect=mutate):
            self.assert_status(409, lambda: service.query(self.payload('completion')))

    def test_public_fingerprint_matches_query_and_tracks_configuration(self):
        service = self.service()
        (self.root / 'README.md').write_text('notes')
        payload = self.payload()
        extra = self.project.read('README.md')
        payload['documents'].append({key: extra[key] for key in ('path', 'content', 'revision')})
        payload['documents'][-1]['version'] = 2
        before = service.project_fingerprint()
        self.assertEqual(service.query(payload)['project_fingerprint'], before)
        (self.root / 'pyrightconfig.json').write_text('{"typeCheckingMode":"strict"}')
        self.assertNotEqual(service.project_fingerprint(), before)

    def test_generated_dependency_trees_do_not_become_source_context(self):
        service = self.service()
        for name in ('.m0-tools', '.venv', 'node_modules', '__pycache__', '.vwb'):
            directory = self.root / name
            directory.mkdir()
            (directory / 'secret.py').write_text('ignored')
        result = service.query(self.payload())
        self.assertIn('OPEN=1', result['result']['text'])

    def test_unversioned_diagnostics_do_not_claim_current_dirty_snapshot(self):
        result = self.service('unversioned').query(self.payload(content='dirty = 2'))
        self.assertEqual(result['diagnostics'], [])

    def test_completion_ranges_default_to_current_unicode_identifier(self):
        service = self.service()
        active = {'content': '🦉 tick', 'path': 'src/value.py'}
        result = service._complete([{'label': 'tick'}], active, {'line': 0, 'character': 5})
        self.assertEqual(result['items'][0]['range'], {'start': {'line': 0, 'character': 3},
                                                     'end': {'line': 0, 'character': 7}})
        result = service._complete([{'label': f'x{index}'} for index in range(250)],
                                   {'content': 'x', 'path': 'src/value.py'}, {'line': 0, 'character': 1})
        self.assertEqual(len(result['items']), 200)
        self.assertTrue(result['truncated'])

    def test_completion_fallback_ranges_support_cr_crlf_and_lf_lines(self):
        service = self.service()
        for newline in ('\r', '\r\n', '\n'):
            with self.subTest(newline=repr(newline)):
                active = {'path': 'src/value.py', 'content': '🦉 one' + newline + 'tick'}
                result = service._complete([{'label': 'tick'}], active, {'line': 1, 'character': 2})
                self.assertEqual(len(result['items']), 1)
                self.assertEqual(result['items'][0]['range'], {'start': {'line': 1, 'character': 0},
                                                             'end': {'line': 1, 'character': 4}})

    def test_completion_filters_full_inventory_before_applying_result_limit(self):
        service = self.service()
        inventory = [{'label': f'builtin_{index}'} for index in range(350)]
        inventory += [{'label': 'std'}, {'label': 'step'}, {'label': 'step_later'}]
        active = {'path': 'src/value.vhd', 'content': 'prefix STEp suffix'}
        result = service._complete(inventory, active, {'line': 0, 'character': 9})
        self.assertEqual([item['label'] for item in result['items']], ['step', 'std', 'step_later'])
        self.assertFalse(result['truncated'])
        self.assertEqual(result['items'][0]['range'], {'start': {'line': 0, 'character': 7},
                                                     'end': {'line': 0, 'character': 11}})
        for path in ('src/value.py', 'src/value.sv'):
            result = service._complete(inventory, {**active, 'path': path}, {'line': 0, 'character': 9})
            self.assertEqual(result['items'], [])

    def test_completion_uses_filter_text_and_retains_candidates_beyond_raw_limit(self):
        service = self.service()
        inventory = [{'label': f'builtin_{index}'} for index in range(250)]
        inventory += [{'label': 'pretty_call(arg)', 'filterText': 'tick', 'insertText': 'tick'},
                      {'label': 'tick_more', 'sortText': '001'}]
        active = {'path': 'src/value.py', 'content': '🦉 tick'}
        result = service._complete(inventory, active, {'line': 0, 'character': 5})
        self.assertEqual([item['label'] for item in result['items']], ['pretty_call(arg)', 'tick_more'])
        self.assertEqual(result['items'][0]['insert_text'], 'tick')
        with self.assertRaisesRegex(Exception, '10000'):
            service._complete([{'label': 'tick'}] * 10001, active, {'line': 0, 'character': 5})

    def test_timeout_and_busy_and_closed_services_fail_cleanly(self):
        service = self.service('slow')
        with mock.patch('companion.editor.TIMEOUT', .9):
            before = time.monotonic()
            self.assert_status(504, lambda: service.query(self.payload()))
            self.assertLess(time.monotonic() - before, 2)
        service._lock.acquire()
        try:
            self.assert_status(429, lambda: service.query(self.payload()))
        finally:
            service._lock.release()
        service.close()
        self.assert_status(503, lambda: service.query(self.payload()))


if __name__ == '__main__':
    unittest.main()
