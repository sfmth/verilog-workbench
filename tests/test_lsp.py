"""Transport/safety unit checks use a fake server, never editor-feasibility evidence."""
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

from companion.lsp import (LspClient, LspError, LspResponseError, _offset,
    encode_message, preview_workspace_edit, read_message, snapshot_files,
    semantic_references_with_definition)

REPO = Path(__file__).resolve().parents[1]
FAKE = r'''
import sys,time
sys.path.insert(0, sys.argv[1])
from companion.lsp import read_message,encode_message
def send(value):
    data=encode_message(value)
    # Several writes deliberately fragment both header and Unicode body.
    for start in range(0,len(data),7):
        sys.stdout.buffer.write(data[start:start+7]);sys.stdout.buffer.flush()
while True:
    message=read_message(sys.stdin.buffer)
    method=message.get('method')
    if method=='exit': break
    if 'id' not in message: continue
    if method=='initialize': result={'capabilities': {'hoverProvider':True}}
    elif method=='echo': result=message['params']
    elif method=='slow': time.sleep(5);continue
    elif method=='server-request':
        send({'jsonrpc':'2.0','id':'config','method':'workspace/configuration','params':{'items':[{'section':'python.analysis'}]}})
        reply=read_message(sys.stdin.buffer);result=reply['result']
    elif method=='shutdown': result=None
    else:
        send({'jsonrpc':'2.0','id':message['id'],'error':{'code':-32601,'message':'unsupported'}});continue
    send({'jsonrpc':'2.0','id':message['id'],'result':result})
'''


class FramingTests(unittest.TestCase):
    def test_unicode_content_length_counts_bytes(self):
        value = {"jsonrpc": "2.0", "result": "μ🦉", "id": 1}
        self.assertEqual(read_message(BytesIO(encode_message(value))), value)

    def test_partial_body_read(self):
        class Fragmented(BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 2))
        value = {"jsonrpc": "2.0", "result": "μ", "id": 1}
        self.assertEqual(read_message(Fragmented(encode_message(value))), value)

    def test_invalid_messages_fail_closed(self):
        for message in (b"Content-Length: -1\r\n\r\n", b"Content-Length: 4\n\nnull",
                        b"Content-Length: 4\r\nContent-Length: 4\r\n\r\nnull",
                        b"Content-Length: 4\r\n\r\n[]  ", b"Content-Length: 9\r\n\r\n{}"):
            with self.subTest(message=message), self.assertRaises((LspError, EOFError)):
                read_message(BytesIO(message))


class LifecycleTests(unittest.TestCase):
    def test_initialize_request_server_configuration_and_shutdown(self):
        with tempfile.TemporaryDirectory() as folder:
            with LspClient([sys.executable, "-u", "-c", FAKE, str(REPO)], folder,
                           timeout=2, configuration={"python": {"analysis": {"mode": "basic"}}}) as client:
                self.assertTrue(client.initialize()["capabilities"]["hoverProvider"])
                self.assertEqual(client.request("echo", {"value": "μ🦉"}), {"value": "μ🦉"})
                self.assertEqual(client.request("server-request"), [{"mode": "basic"}])
                with self.assertRaises(LspResponseError) as error:
                    client.request("missing")
                self.assertEqual(error.exception.error["code"], -32601)
            self.assertEqual(client.process.returncode, 0)
            self.assertFalse(client._reader.is_alive())
            client.close()

    def test_timeout_is_bounded_and_server_is_reaped(self):
        with tempfile.TemporaryDirectory() as folder:
            start = time.monotonic()
            with LspClient([sys.executable, "-u", "-c", FAKE, str(REPO)], folder, timeout=1) as client:
                client.initialize()
                with self.assertRaises(TimeoutError):
                    client.request("slow", timeout=.2)
            self.assertIsNotNone(client.process.returncode)
            self.assertLess(time.monotonic() - start, 4)

    def test_large_document_to_nonreading_server_has_bounded_send(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "large.sv"
            path.write_text("// padded document\n" * 200000)
            start = time.monotonic()
            with LspClient([sys.executable, "-c", "import time; time.sleep(20)"],
                           folder, timeout=.2) as client:
                with self.assertRaisesRegex(TimeoutError, "writing"):
                    client.open_document(path, "systemverilog")
            self.assertIsNotNone(client.process.returncode)
            self.assertFalse(client._writer.is_alive())
            self.assertLess(time.monotonic() - start, 3)

    def test_request_deadline_also_bounds_writing_large_message(self):
        with tempfile.TemporaryDirectory() as folder:
            start = time.monotonic()
            with LspClient([sys.executable, "-c", "import time; time.sleep(20)"],
                           folder, timeout=1) as client:
                with self.assertRaisesRegex(TimeoutError, "writing"):
                    client.request("large", {"text": "x" * 1048576}, timeout=.1)
            self.assertIsNotNone(client.process.returncode)
            self.assertFalse(client._writer.is_alive())
            self.assertLess(time.monotonic() - start, 2)


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "μ space.py"
        self.path.write_bytes("🦉 = 1\r\nvalue = 2\r\n".encode())
        self.uri = self.path.as_uri()
        self.snapshots = snapshot_files(self.root)

    def edit(self, start=0, end=5):
        return {"changes": {self.uri: [{"range": {
            "start": {"line": 1, "character": start},
            "end": {"line": 1, "character": end}}, "newText": "renamed"}]}}

    def test_preview_preserves_crlf_and_never_writes(self):
        result = preview_workspace_edit(self.root, self.edit(), self.snapshots)
        self.assertEqual(result[self.uri]["text"], "🦉 = 1\r\nrenamed = 2\r\n")
        self.assertEqual(self.path.read_bytes(), self.snapshots[self.uri])

    def test_utf16_position_and_invalid_ranges(self):
        self.assertEqual(_offset("🦉 = 1", {"line": 0, "character": 2}), 1)
        for pos in ({"line": 0, "character": 1}, {"line": 1, "character": 0},
                    {"line": -1, "character": 0}, {"line": 0, "character": 100}):
            with self.subTest(pos=pos), self.assertRaises(LspError):
                _offset("🦉 = 1", pos)

    def test_stale_disk_is_conflict(self):
        self.path.write_text("external edit")
        with self.assertRaisesRegex(LspError, "conflict"):
            preview_workspace_edit(self.root, self.edit(), self.snapshots)

    def test_version_conflict(self):
        edits = self.edit()["changes"][self.uri]
        edit = {"documentChanges": [{"textDocument": {"uri": self.uri, "version": 2}, "edits": edits}]}
        with self.assertRaisesRegex(LspError, "version conflict"):
            preview_workspace_edit(self.root, edit, self.snapshots, {self.uri: 1})

    def test_overlapping_or_external_edits_rejected(self):
        edit = self.edit()
        edit["changes"][self.uri] *= 2
        with self.assertRaisesRegex(LspError, "Overlapping"):
            preview_workspace_edit(self.root, edit, self.snapshots)
        with tempfile.TemporaryDirectory() as outside:
            other = Path(outside) / "external.py"
            other.write_text("test")
            edit = {"changes": {other.as_uri(): self.edit()["changes"][self.uri]}}
            with self.assertRaisesRegex(LspError, "escapes"):
                preview_workspace_edit(self.root, edit, self.snapshots)

    def test_resource_operation_rejected(self):
        with self.assertRaisesRegex(LspError, "resource operations"):
            preview_workspace_edit(self.root, {"documentChanges": [{"kind": "delete", "uri": self.uri}]}, self.snapshots)

    def test_merge_independently_resolved_definition_deduplicates(self):
        definition = {"uri": self.uri, "range": {"start": {"line": 1, "character": 0},
                      "end": {"line": 1, "character": 5}}}
        reference = {"uri": self.uri, "range": {"start": {"line": 0, "character": 0},
                     "end": {"line": 0, "character": 2}}}
        merged = semantic_references_with_definition(self.root, self.snapshots,
            [reference, definition], [definition])
        self.assertEqual(len(merged), 2)
        self.assertIn(definition, merged)
        self.assertIn(reference, merged)

    def test_reference_merge_rejects_ambiguous_definition_and_stale_file(self):
        a = {"uri": self.uri, "range": {"start": {"line": 1, "character": 0},
             "end": {"line": 1, "character": 5}}}
        b = {"uri": self.uri, "range": {"start": {"line": 0, "character": 0},
             "end": {"line": 0, "character": 2}}}
        with self.assertRaisesRegex(LspError, "ambiguous"):
            semantic_references_with_definition(self.root, self.snapshots, [], [a, b])
        self.path.write_text("changed")
        with self.assertRaisesRegex(LspError, "stale"):
            semantic_references_with_definition(self.root, self.snapshots, [b], [a])


if __name__ == "__main__":
    unittest.main()
