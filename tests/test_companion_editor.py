"""Real HTTP/editor transaction integration with an explicitly fake LSP bridge.

The deterministic bridge proves API plumbing and filesystem protections only.
Real language-server semantics are exercised by scripts/probe_editor_workspace.mjs.
"""
import http.client
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import quote

from companion.editor import EditorService as RealEditorService, METHODS
from companion.projects import ProjectError
from companion.server import create_server

ORIGIN = "https://sfmth.github.io"
PRIVATE_ARG = "--local-editor-config=private-test-option"


class FakeEditor(RealEditorService):
    """Test-only rename fixture; never launches or claims a semantic server."""

    def __init__(self, project, config=None):
        super().__init__(project, config)
        self.query_calls = []
        self.after_query = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def query(self, payload):
        self.query_calls.append(payload)
        if (not isinstance(payload, dict)
                or set(payload) - {"operation", "path", "position", "documents", "new_name"}
                or payload.get("operation") not in METHODS):
            raise ProjectError(400, "invalid fake editor query")
        operation = payload["operation"]
        with self.project.lock:
            documents = self._documents(payload)
            relative = self._path(payload.get("path")).relative_to(self.project.root).as_posix()
            if relative not in documents:
                raise ProjectError(400, "active document snapshot required")
            active = documents[relative]
            if operation == "rename":
                name = payload.get("new_name")
                if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    raise ProjectError(400, "invalid fake rename name")
                if any(doc["disk"] is None or doc["content"].encode("utf-8") != doc["disk"]
                       for doc in documents.values()):
                    raise ProjectError(409, "save documents before rename")
                files = []
                # Deliberately fixed fixture substitution, not semantic rename.
                for path in ("src/counter.sv", "test/test_counter.py"):
                    item = self.project.read(path)
                    files.append({"path": path, "revision": item["revision"],
                                  "content": item["content"].replace("counter", name),
                                  "edit_count": item["content"].count("counter")})
                result = {"files": files}
            else:
                result = {"text": "explicit test bridge"}
            response = {"operation": operation, "language": "systemverilog", "path": relative,
                        "version": active["version"], "revision": active["revision"],
                        "source_revision": self.project.source_revision(),
                        "project_fingerprint": self.project_fingerprint(),
                        "result": result, "diagnostics": []}
        self.entered.set()
        if not self.release.wait(5):
            raise ProjectError(504, "test bridge release timed out")
        if self.after_query:
            self.after_query()
        return response


class CompanionEditorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "src").mkdir()
        (self.root / "test").mkdir()
        (self.root / "src/counter.sv").write_bytes(b"module counter;\r\nendmodule\r\n")
        (self.root / "test/test_counter.py").write_bytes(b"# counter fixture\n")
        self.runner_entered = threading.Event()
        self.runner_release = threading.Event()

        def runner(*_args, **_kwargs):
            self.runner_entered.set()
            if not self.runner_release.wait(5):
                raise TimeoutError("test runner release timed out")
            return SimpleNamespace(returncode=0, stdout="explicit test runner", stderr="")

        config = {language: {"command": [sys.executable, PRIVATE_ARG]}
                  for language in ("systemverilog", "sv_refactor", "vhdl", "python")}
        self.bridge_patch = patch("companion.editor.EditorService", FakeEditor)
        self.bridge_patch.start()
        self.addCleanup(self.bridge_patch.stop)
        self.server = create_server(self.root, ORIGIN, runner=runner, editor_config=config)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.token = None

    def tearDown(self):
        self.server.state.editor.release.set()
        self.runner_release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.directory.cleanup()

    def request(self, method, path, body=None, *, token=True, origin=ORIGIN, headers=None):
        custom = {"Origin": origin} if origin is not None else {}
        if token and self.token:
            custom["Authorization"] = "Bearer " + self.token
        raw = None
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            custom["Content-Type"] = "application/json"
        custom.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request(method, path, body=raw, headers=custom)
            response = connection.getresponse()
            content = response.read()
            status, response_headers = response.status, dict(response.getheaders())
        finally:
            connection.close()
        if content and response_headers.get("Content-Type", "").startswith("application/json"):
            content = json.loads(content)
        return status, content, response_headers

    def pair(self):
        status, body, _ = self.request("POST", "/v1/pair",
                                       {"code": self.server.state.pairing_code}, token=False)
        self.assertEqual(status, 200, body)
        self.token = body["token"]

    def query_body(self, *, operation="rename", new_name="renamed", version=7):
        item = self.server.state.project.read("src/counter.sv")
        return {"operation": operation, "path": item["path"], "position": {"line": 0, "character": 9},
                "new_name": new_name, "documents": [{"path": item["path"], "content": item["content"],
                                                       "revision": item["revision"], "version": version}]}

    def rename(self):
        status, body, _ = self.request("POST", "/v1/editor/query", self.query_body())
        self.assertEqual(status, 200, body)
        return body

    def assert_original(self):
        self.assertEqual((self.root / "src/counter.sv").read_bytes(), b"module counter;\r\nendmodule\r\n")
        self.assertEqual((self.root / "test/test_counter.py").read_bytes(), b"# counter fixture\n")

    def interrupted(self):
        plan = self.rename()["plan"]
        engine = self.server.state.edits
        replace = engine._replace_file
        count = 0

        def injected(target, staged):
            nonlocal count
            count += 1
            if count == 2:
                raise SystemExit("test interruption after first file")
            replace(target, staged)

        # Inject the abrupt exit in-process to avoid an intentionally broken
        # HTTP connection; recovery itself is exercised through real HTTP.
        with patch.object(engine, "_replace_file", side_effect=injected):
            with self.assertRaises(SystemExit):
                engine.apply(plan["id"])
        self.assertTrue(engine.status()["recovery_required"])
        return plan

    def test_all_editor_endpoints_require_session_origin_and_host(self):
        endpoints = [("GET", "/v1/editor/capabilities", None),
                     ("POST", "/v1/editor/query", {}), ("POST", "/v1/editor/apply", {}),
                     ("POST", "/v1/editor/undo", {}), ("POST", "/v1/editor/recover", {})]
        for method, path, body in endpoints:
            with self.subTest(path=path):
                self.assertEqual(self.request(method, path, body)[0], 401)
        self.pair()
        for method, path, body in endpoints:
            with self.subTest(path=path):
                self.assertEqual(self.request(method, path, body, token=False)[0], 401)
                self.assertEqual(self.request(method, path, body, origin="https://evil.example")[0], 403)
                self.assertEqual(self.request(method, path, body, headers={"Host": "evil.example"})[0], 403)
                self.assertEqual(self.request(method, path, body, headers={"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.server.state.editor.query_calls, [])
        self.assert_original()

    def test_capabilities_expose_discovery_without_argv_or_private_configuration(self):
        self.pair()
        status, body, headers = self.request("GET", "/v1/editor/capabilities")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["position_encoding"], "utf-16")
        self.assertEqual(body["transactions"], {"recovery_required": False, "files": []})
        self.assertNotIn(PRIVATE_ARG, json.dumps(body))
        self.assertNotIn(str(self.root), json.dumps(body))
        self.assertNotIn("command", json.dumps(body))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)

    def test_rename_preview_apply_and_undo_use_server_ids_and_real_files(self):
        self.pair()
        response = self.rename()
        self.assertEqual(response["operation"], "rename")
        self.assertEqual(response["path"], "src/counter.sv")
        self.assertEqual(response["version"], 7)
        self.assertEqual(response["revision"], self.server.state.project.read("src/counter.sv")["revision"])
        plan = response["plan"]
        self.assertRegex(plan["id"], r"^[A-Za-z0-9_-]{16,128}$")
        self.assertEqual(len(plan["files"]), 2)
        self.assert_original()
        status, result, _ = self.request("POST", "/v1/editor/apply", {"id": plan["id"]})
        self.assertEqual(status, 200, result)
        self.assertEqual((self.root / "src/counter.sv").read_bytes(), b"module renamed;\r\nendmodule\r\n")
        self.assertEqual((self.root / "test/test_counter.py").read_bytes(), b"# renamed fixture\n")
        self.assertEqual(len(result["files"]), 2)
        for item in result["files"]:
            current = self.server.state.project.read(item["path"])
            self.assertEqual((item["content"], item["revision"]), (current["content"], current["revision"]))
        self.assertEqual(self.request("POST", "/v1/editor/apply", {"id": plan["id"]})[0], 409)
        status, undone, _ = self.request("POST", "/v1/editor/undo", {"id": result["undo_id"]})
        self.assertEqual(status, 200, undone)
        self.assert_original()
        self.assertEqual(self.request("POST", "/v1/editor/undo", {"id": result["undo_id"]})[0], 409)

    def test_apply_rejects_unrelated_source_snapshot_change(self):
        self.pair()
        plan = self.rename()["plan"]
        (self.root / "src/other.sv").write_text("module other; endmodule\n", encoding="utf-8")
        self.assertEqual(self.request("POST", "/v1/editor/apply", {"id": plan["id"]})[0], 409)
        self.assert_original()

    def test_apply_rejects_changed_project_configuration_even_with_same_source_hash(self):
        self.pair()
        before = self.server.state.project.source_revision()
        plan = self.rename()["plan"]
        (self.root / "pyrightconfig.json").write_text('{"pythonVersion":"3.12"}\n', encoding="utf-8")
        self.assertEqual(before, self.server.state.project.source_revision())
        self.assertEqual(self.request("POST", "/v1/editor/apply", {"id": plan["id"]})[0], 409)
        self.assert_original()

    def test_rename_rechecks_source_and_configuration_after_bridge_returns(self):
        self.pair()
        editor = self.server.state.editor
        editor.after_query = lambda: (self.root / "src/other.sv").write_text("outside change", encoding="utf-8")
        self.assertEqual(self.request("POST", "/v1/editor/query", self.query_body())[0], 409)
        self.assertFalse(self.server.state.editor_busy)
        (self.root / "src/other.sv").unlink()
        editor.after_query = lambda: (self.root / "pyrightconfig.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.request("POST", "/v1/editor/query", self.query_body())[0], 409)
        self.assertFalse(self.server.state.editor_busy)
        self.assertEqual(self.server.state.editor_plans, {})
        self.assert_original()

    def test_undo_rejects_external_project_change_without_touching_edited_files(self):
        self.pair()
        plan = self.rename()["plan"]
        status, applied, _ = self.request("POST", "/v1/editor/apply", {"id": plan["id"]})
        self.assertEqual(status, 200, applied)
        (self.root / "test/test_counter.py").write_bytes(b"external work after rename\n")
        self.assertEqual(self.request("POST", "/v1/editor/undo", {"id": applied["undo_id"]})[0], 409)
        self.assertEqual((self.root / "src/counter.sv").read_bytes(), b"module renamed;\r\nendmodule\r\n")
        self.assertEqual((self.root / "test/test_counter.py").read_bytes(), b"external work after rename\n")

    def test_native_job_excludes_rename_apply_undo_and_recover(self):
        self.pair()
        plan = self.rename()["plan"]
        status, job, _ = self.request("POST", "/v1/jobs", {"command": "list"})
        self.assertEqual(status, 202, job)
        self.assertTrue(self.runner_entered.wait(5))
        self.assertEqual(self.request("POST", "/v1/editor/query", self.query_body())[0], 409)
        for operation, body in (("apply", {"id": plan["id"]}), ("undo", {"id": "unknown_editor_id_123456"}), ("recover", {})):
            with self.subTest(operation=operation):
                self.assertEqual(self.request("POST", "/v1/editor/" + operation, body)[0], 409)
        self.assert_original()
        self.runner_release.set()
        self.server.state.jobs[job["id"]]["thread"].join(5)
        self.assertEqual(self.request("POST", "/v1/editor/apply", {"id": plan["id"]})[0], 200)

    def test_busy_rename_excludes_native_jobs_and_another_mutation(self):
        self.pair()
        editor = self.server.state.editor
        editor.entered.clear()
        editor.release.clear()
        response = []
        thread = threading.Thread(target=lambda: response.append(self.request("POST", "/v1/editor/query", self.query_body())))
        thread.start()
        self.assertTrue(editor.entered.wait(5))
        try:
            self.assertEqual(self.request("POST", "/v1/jobs", {"command": "list"})[0], 409)
            self.assertEqual(self.request("POST", "/v1/editor/apply", {"id": "unknown_editor_id_123456"})[0], 409)
            self.assertEqual(self.request("POST", "/v1/editor/recover", {})[0], 409)
        finally:
            editor.release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(response[0][0], 200, response)
        self.assertFalse(self.runner_entered.is_set())

    def test_pending_recovery_blocks_jobs_queries_and_ordinary_saves_then_recovers_over_http(self):
        self.pair()
        self.interrupted()
        status, capabilities, _ = self.request("GET", "/v1/editor/capabilities")
        self.assertEqual(status, 200, capabilities)
        self.assertTrue(capabilities["transactions"]["recovery_required"])
        self.assertEqual(self.request("POST", "/v1/jobs", {"command": "list"})[0], 409)
        self.assertEqual(self.request("POST", "/v1/editor/query", self.query_body())[0], 409)
        current = self.server.state.project.read("src/counter.sv")
        save = {"path": current["path"], "revision": current["revision"], "content": "ordinary browser save"}
        self.assertEqual(self.request("PUT", "/v1/project/file", save)[0], 409)
        status, recovered, _ = self.request("POST", "/v1/editor/recover", {})
        self.assertEqual(status, 200, recovered)
        self.assertFalse(recovered["recovery_required"])
        self.assert_original()
        current = self.server.state.project.read("src/counter.sv")
        save = {"path": current["path"], "revision": current["revision"], "content": current["content"]}
        self.assertEqual(self.request("PUT", "/v1/project/file", save)[0], 200)

    def test_recovery_backups_are_not_project_files_or_downloadable_artifacts(self):
        self.pair()
        self.interrupted()
        prefix = ".vwb/editor-recovery"
        for private in (prefix + "/.owner", prefix + "/pending/manifest.json", prefix + "/pending/0.before"):
            with self.subTest(private=private):
                self.assertEqual(self.request("GET", "/v1/project/file?path=" + quote(private))[0], 403)
                self.assertEqual(self.request("GET", "/v1/artifacts/file?path=" + quote(private))[0], 403)
        for endpoint in ("/v1/project/files", "/v1/artifacts"):
            status, listing, _ = self.request("GET", endpoint)
            self.assertEqual(status, 200, listing)
            self.assertFalse(any(item["path"].startswith(prefix) for item in listing["files"]))

    def test_recovery_conflict_keeps_external_work_and_retained_backups(self):
        self.pair()
        self.interrupted()
        target = self.root / "src/counter.sv"
        target.write_bytes(b"external post-interruption work\n")
        status, body, _ = self.request("POST", "/v1/editor/recover", {})
        self.assertEqual(status, 409, body)
        self.assertEqual(target.read_bytes(), b"external post-interruption work\n")
        self.assertTrue(self.server.state.edits.status()["recovery_required"])
        self.assertTrue((self.server.state.edits._pending / "0.before").is_file())

    def test_unknown_ids_and_arbitrary_edit_bodies_are_rejected(self):
        self.pair()
        for operation in ("apply", "undo"):
            endpoint = "/v1/editor/" + operation
            with self.subTest(operation=operation):
                self.assertEqual(self.request("POST", endpoint, {"id": "unknown_editor_id_123456"})[0], 409)
                for invalid in ({}, {"id": "tiny"}, {"id": None}, {"id": "bad/path_editor_id_123456"},
                                {"id": "unknown_editor_id_123456", "content": "injected"},
                                {"files": [{"path": "src/counter.sv", "content": "injected"}]}):
                    self.assertEqual(self.request("POST", endpoint, invalid)[0], 400, invalid)
        for invalid in ({"id": "unknown_editor_id_123456"}, {"files": []}, {"content": "injected"}):
            self.assertEqual(self.request("POST", "/v1/editor/recover", invalid)[0], 400)
        self.assert_original()

    def test_browser_cannot_supply_language_server_commands_or_configuration(self):
        self.pair()
        for field in ("command", "argv", "editor_config", "initialization_options", "configuration"):
            with self.subTest(field=field):
                payload = {**self.query_body(), field: ["untrusted", "--argv"]}
                self.assertEqual(self.request("POST", "/v1/editor/query", payload)[0], 400)
        self.assertEqual(self.request("POST", "/v1/editor/config", {"command": ["untrusted"]})[0], 404)
        self.assertFalse(self.server.state.editor_busy)
        self.assert_original()

    def test_stale_or_dirty_document_snapshot_cannot_create_plan(self):
        self.pair()
        payload = self.query_body()
        payload["documents"][0]["revision"] = "0" * 64
        self.assertEqual(self.request("POST", "/v1/editor/query", payload)[0], 409)
        payload = self.query_body()
        payload["documents"][0]["content"] += "// dirty browser draft\n"
        self.assertEqual(self.request("POST", "/v1/editor/query", payload)[0], 409)
        self.assertEqual(self.server.state.editor_plans, {})
        self.assert_original()


class EditorLocalConfigurationTests(unittest.TestCase):
    def test_cli_reads_explicit_local_configuration_and_passes_it_only_at_startup(self):
        from companion.__main__ import main
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "editor.json"
            config = {"python": {"command": [sys.executable, PRIVATE_ARG]}, "vhdl": False}
            path.write_text(json.dumps(config), encoding="utf-8")
            server = MagicMock()
            server.server_port = 12345
            server.state.pairing_code = "test-pairing-code"
            server.serve_forever.side_effect = KeyboardInterrupt
            with patch("companion.__main__.create_server", return_value=server) as create, \
                    patch("companion.__main__.signal.signal"), patch("sys.stdout", new_callable=io.StringIO) as output:
                result = main(["--project", directory, "--origin", ORIGIN, "--editor-config", str(path)])
            self.assertEqual(result, 0)
            self.assertEqual(create.call_args.kwargs["editor_config"], config)
            self.assertNotIn(PRIVATE_ARG, output.getvalue())
            server.server_close.assert_called_once()

    def test_cli_refuses_oversized_or_invalid_local_configuration_before_server_creation(self):
        from companion.__main__ import main
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "editor.json"
            for data in (b"x" * 65537, b"not json"):
                with self.subTest(size=len(data)):
                    path.write_bytes(data)
                    with patch("companion.__main__.create_server") as create, patch("sys.stderr", new_callable=io.StringIO):
                        result = main(["--project", directory, "--origin", ORIGIN, "--editor-config", str(path)])
                    self.assertEqual(result, 2)
                    create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
