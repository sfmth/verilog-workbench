"""Exercise actual HTTP handling, grants and the native job adapter."""
import concurrent.futures
from collections import deque
import http.client
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote

from companion.projects import MAX_FILE_BYTES, ProjectError, ProjectGrant
from companion.server import MAX_BODY_BYTES, create_server

ORIGIN = "https://sfmth.github.io"


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "src").mkdir()
        (self.root / "test").mkdir()
        (self.root / "src" / "counter.sv").write_bytes(b"module counter(input a, output b);\r\nassign b=a;\r\nendmodule\r\n")
        self.server = create_server(self.root, ORIGIN)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.token = None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.directory.cleanup()

    def request(self, method, path, body=None, *, token=True, origin=ORIGIN, headers=None, raw=None):
        custom = {"Origin": origin} if origin is not None else {}
        if token and self.token:
            custom["Authorization"] = "Bearer " + self.token
        if body is not None:
            raw = json.dumps(body).encode()
            custom["Content-Type"] = "application/json"
        custom.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request(method, path, body=raw, headers=custom)
        response = connection.getresponse()
        content = response.read()
        status, response_headers = response.status, dict(response.getheaders())
        connection.close()
        if content and response_headers.get("Content-Type", "").startswith("application/json"):
            content = json.loads(content)
        return status, content, response_headers

    def pair(self):
        status, data, _ = self.request("POST", "/v1/pair", {"code": self.server.state.pairing_code}, token=False)
        self.assertEqual(status, 200, data)
        self.token = data["token"]
        return data

    def read(self, path="src/counter.sv"):
        status, data, _ = self.request("GET", "/v1/project/file?path=" + quote(path))
        self.assertEqual(status, 200, data)
        return data

    def wait_job(self, job, timeout=10):
        deadline = time.monotonic() + timeout
        while job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
            status, job, _ = self.request("GET", "/v1/jobs/" + job["id"])
            self.assertEqual(status, 200, job)
        self.assertNotEqual(job["status"], "running", job)
        return job

    def project_flow(self, *, failing=False):
        profile = self.root / "native-verification-profile"
        profile.mkdir()
        (profile / "vwb_http_profile_dependency.py").write_text("VALUE = 'declared native profile'\n")
        executable = "python3" if shutil.which("python3", path=str(Path(sys.executable).parent) + os.pathsep + os.defpath) else "python"
        xml = '<testsuite tests="2"><testcase name="original_data_assertions"/>'
        xml += '<testcase name="original_protocol_assertions">'
        if failing:
            xml += '<failure message="upstream assertion failed"/>'
        xml += '</testcase></testsuite>'
        (self.root / "test/original_project_bench.py").write_text(
            "from pathlib import Path\nimport os\nimport vwb_http_profile_dependency as dependency\n"
            "assert dependency.VALUE == 'declared native profile'\n"
            "assert os.environ['VIRTUAL_ENV'] == " + repr(str(profile)) + "\n"
            "Path('native-results.xml').write_text(" + repr(xml) + ")\n"
            "print('ORIGINAL_PROJECT_BENCH_FINISHED')\n")
        flow = {"id": "http-upstream", "driver": "python", "simulator": "icarus",
                "argv": [executable, "test/original_project_bench.py"],
                "environment": {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.defpath,
                                "PYTHONPATH": str(profile), "VIRTUAL_ENV": str(profile),
                                "PYTHONNOUSERSITE": "1"},
                "requirements": {"tools": [executable], "python_modules": ["vwb_http_profile_dependency"]},
                "expected": {"kind": "junit", "paths": ["native-results.xml"], "minimum_tests": 2}}
        (self.root / ".vwb-flows.json").write_text(json.dumps({
            "schema_version": 1, "flows": [flow],
            "source_files": ["src/counter.sv", "test/original_project_bench.py",
                             "native-verification-profile/vwb_http_profile_dependency.py"]}))
        return flow

    def test_health_does_not_expose_grant_or_credentials(self):
        status, data, _ = self.request("GET", "/v1/health", origin=None)
        self.assertEqual(status, 200)
        self.assertEqual(set(data), {"status", "protocol", "version"})

    def test_project_identity_scopes_recovery_without_exposing_absolute_path(self):
        paired = self.pair()["project"]
        self.assertRegex(paired["id"], r"^[a-f0-9]{64}$")
        self.assertEqual(paired["id"], ProjectGrant(self.root).summary()["id"])
        self.assertNotIn(str(self.root), json.dumps(paired))
        sibling = self.root / "other" / self.root.name
        sibling.mkdir(parents=True)
        self.assertNotEqual(paired["id"], ProjectGrant(sibling).summary()["id"])

    def test_numeric_loopback_startup_never_reverse_resolves_dns(self):
        with patch("socket.getfqdn", side_effect=AssertionError("loopback startup must not query DNS")):
            server = create_server(self.root, ORIGIN)
        try:
            self.assertEqual(server.server_name, "127.0.0.1")
            self.assertGreater(server.server_port, 0)
        finally:
            server.server_close()

    def test_exact_origin_host_and_bearer_required(self):
        self.pair()
        for kwargs in ({"origin": "https://evil.example"}, {"origin": None},
                       {"headers": {"Host": "evil.example"}}, {"token": False},
                       {"headers": {"Authorization": "Bearer wrong"}}):
            with self.subTest(kwargs=kwargs):
                status, _, _ = self.request("GET", "/v1/project/files", **kwargs)
                self.assertIn(status, (401, 403))
        status, _, _ = self.request("GET", "/v1/project/files?token=" + self.token, token=False)
        self.assertEqual(status, 401)
        before = (self.root / "src/counter.sv").read_bytes()
        payload = {"path": "src/counter.sv", "revision": self.read()["revision"], "content": "unauthorized"}
        self.assertEqual(self.request("PUT", "/v1/project/file", payload, token=False)[0], 401)
        self.assertEqual(self.request("PUT", "/v1/project/file", payload, origin="https://evil.example")[0], 403)
        self.assertEqual((self.root / "src/counter.sv").read_bytes(), before)

    def test_capabilities_remain_authenticated_and_report_discovery(self):
        self.assertEqual(self.request("GET", "/v1/capabilities")[0], 401)
        self.pair()
        with patch("companion.toolchains.capability_report", return_value={"proof": "discovered-not-executed"}):
            status, payload, _ = self.request("GET", "/v1/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(payload["toolchains"]["proof"], "discovered-not-executed")
        self.assertEqual(set(payload["commands"]), {"list", "doctor", "test", "wave", "lint", "synth", "formal", "fpga", "flow"})
        self.assertEqual(payload["limits"]["job_seconds"], 300)
        self.assertEqual(payload["limits"]["project_flow_seconds"], 7200)

    def test_pair_one_time_expiry_and_attempts(self):
        self.pair()
        self.assertEqual(self.request("POST", "/v1/pair", {"code": self.server.state.pairing_code})[0], 409)
        self.server.state.token = None
        self.server.state.pairing_expires_at = time.monotonic() - 1
        self.assertEqual(self.request("POST", "/v1/pair", {"code": "wrong"})[0], 410)
        self.server.state.pairing_expires_at = time.monotonic() + 300
        self.server.state.pairing_attempts = 0
        for _ in range(5):
            self.assertEqual(self.request("POST", "/v1/pair", {"code": "\u2764"})[0], 401)
        self.assertEqual(self.request("POST", "/v1/pair", {"code": self.server.state.pairing_code})[0], 429)

    def test_preflight_has_no_pairing_side_effect(self):
        status, _, headers = self.request("OPTIONS", "/v1/pair", headers={
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type, authorization",
            "Access-Control-Request-Private-Network": "true"})
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(headers["Access-Control-Allow-Private-Network"], "true")
        self.assertIsNone(self.server.state.token)
        self.assertEqual(self.server.state.pairing_attempts, 0)
        self.assertEqual(self.request("OPTIONS", "/v1/pair", origin="https://evil.example")[0], 403)

    def test_authorized_save_preserves_line_bytes_and_mode(self):
        self.pair()
        target = self.root / "src/counter.sv"
        target.chmod(0o640)
        old_mode = stat.S_IMODE(target.stat().st_mode)
        data = self.read()
        self.assertIn("\r\n", data["content"])
        content = data["content"].replace("b=a", "b=~a")
        status, saved, _ = self.request("PUT", "/v1/project/file", {"path": data["path"], "content": content, "revision": data["revision"]})
        self.assertEqual(status, 200, saved)
        self.assertEqual(target.read_bytes(), content.encode())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), old_mode)
        self.assertNotEqual(saved["revision"], data["revision"])
        self.assertEqual(list(target.parent.glob(".vwb-save-*")), [])

    def test_external_conflict_new_file_and_concurrent_save(self):
        self.pair()
        data = self.read()
        target = self.root / data["path"]
        target.write_bytes(b"changed externally\n")
        payload = {"path": data["path"], "content": "browser version\n", "revision": data["revision"]}
        self.assertEqual(self.request("PUT", "/v1/project/file", payload)[0], 409)
        self.assertEqual(target.read_bytes(), b"changed externally\n")
        new = {"path": "src/new.sv", "content": "module new; endmodule\n", "revision": None}
        self.assertEqual(self.request("PUT", "/v1/project/file", new)[0], 200)
        self.assertEqual(self.request("PUT", "/v1/project/file", new)[0], 409)
        data = self.read()
        payload["revision"] = data["revision"]
        with concurrent.futures.ThreadPoolExecutor(2) as executor:
            results = list(executor.map(lambda _: self.request("PUT", "/v1/project/file", payload)[0], range(2)))
        self.assertEqual(sorted(results), [200, 409])

    def test_file_path_and_size_boundaries(self):
        self.pair()
        for path in ("../outside", "/etc/passwd", ".git/config", "src/../../outside", "C:/Windows", "src\\counter.sv", ".git./config", "src/CON.txt"):
            with self.subTest(path=path):
                status, _, _ = self.request("GET", "/v1/project/file?path=" + quote(path))
                self.assertIn(status, (400, 403))
        (self.root / "src/big.sv").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        self.assertEqual(self.request("GET", "/v1/project/file?path=src/big.sv")[0], 413)
        oversized = {"path": "src/new.sv", "content": "x" * (MAX_FILE_BYTES + 1), "revision": None}
        self.assertEqual(self.request("PUT", "/v1/project/file", oversized)[0], 413)
        status, _, _ = self.request("PUT", "/v1/project/file", raw=b"{}", headers={
            "Content-Type": "application/json", "Content-Length": str(MAX_BODY_BYTES + 1)})
        self.assertEqual(status, 413)

    def test_symlink_and_git_inventory_protection(self):
        self.pair()
        (self.root / ".git").mkdir()
        (self.root / ".git/config").write_text("private")
        (self.root / ".vwb").mkdir()
        (self.root / ".vwb/trace.vcd").write_bytes(b"\0\xffbinary")
        try:
            (self.root / "src/link.sv").symlink_to(self.root / "src/counter.sv")
        except (OSError, NotImplementedError):
            self.skipTest("host does not permit symlink creation")
        self.assertEqual(self.request("GET", "/v1/project/file?path=src/link.sv")[0], 403)
        files = self.request("GET", "/v1/project/files")[1]["files"]
        self.assertEqual([f["path"] for f in files], ["src/counter.sv"])
        self.assertEqual(self.request("GET", "/v1/artifacts")[1]["files"][0]["path"], ".vwb/trace.vcd")
        status, data, headers = self.request("GET", "/v1/artifacts/file?path=.vwb/trace.vcd")
        self.assertEqual((status, data), (200, b"\0\xffbinary"))
        self.assertEqual(headers["Content-Disposition"], "attachment")
        self.assertEqual(self.request("GET", "/v1/artifacts/file?path=src/counter.sv")[0], 403)

    def test_external_link_and_changed_build_grant_are_refused(self):
        self.pair()
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external)
            (outside / "secret").write_text("outside")
            try:
                (self.root / "src/external").symlink_to(outside, target_is_directory=True)
                (self.root / ".vwb").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("host does not permit symlink creation")
            self.assertEqual(self.request("GET", "/v1/project/file?path=src/external/secret")[0], 403)
            self.assertEqual(self.request("GET", "/v1/artifacts")[0], 403)
            self.assertEqual(self.request("POST", "/v1/jobs", {"command": "list"})[0], 403)
            self.assertEqual(self.server.state.jobs, {})

    def test_launch_configuration_confined_and_overrides(self):
        (self.root / ".vwb.json").write_text(json.dumps({"src_dir": "../outside", "test_dir": "test", "build_dir": ".vwb"}))
        with self.assertRaises(ProjectError):
            ProjectGrant(self.root)
        grant = ProjectGrant(self.root, src_dir="src")
        self.assertEqual(grant.src_dir, "src")

    def test_job_options_reject_untyped_unbounded_and_arbitrary_commands(self):
        self.pair()
        bad = [
            {"command": "sh", "options": {}},
            {"command": "list", "options": {"argv": ["echo", "bad"]}},
            {"command": "test", "options": {"modules": "counter"}},
            {"command": "test", "options": {"seed": True}},
            {"command": "test", "options": {"waves": "false"}},
            {"command": "test", "options": {"modules": ["--help"]}},
            {"command": "lint", "options": {"linters": [{}]}},
            {"command": "list", "timeout": True},
            {"command": "list", "timeout": 301},
            {"command": "formal", "options": {"config": "../outside.sby"}},
            {"command": "fpga", "options": {"board": "tangnano9k", "stage": "flash"}},
            {"command": "fpga", "options": {"board": "icebreaker", "program": True}},
        ]
        for payload in bad:
            with self.subTest(payload=payload):
                self.assertIn(self.request("POST", "/v1/jobs", payload)[0], (400, 403))
        self.assertEqual(self.server.state.jobs, {})
        argv = self.server.state.job_argv("fpga", {"board": "tangnano9k", "stage": "flash", "program": True})
        self.assertIn("--stage", argv)
        self.assertEqual(argv[-1], "flash")

    def test_real_vwb_list_job(self):
        self.pair()
        status, job, _ = self.request("POST", "/v1/jobs", {"command": "list", "timeout": 10})
        self.assertEqual(status, 202, job)
        deadline = time.monotonic() + 10
        while job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
            status, job, _ = self.request("GET", "/v1/jobs/" + job["id"])
            self.assertEqual(status, 200)
        self.assertEqual(job["status"], "passed", job)
        self.assertEqual(job["returncode"], 0, job)
        self.assertFalse(job["stale"])
        self.assertEqual(job["source_revision"], job["source_revision_after"])
        self.assertIn("counter", job["stdout"])
        self.assertIsInstance(json.loads(job["stdout"]), (dict, list))

    def test_project_flow_adapter_only_accepts_names_and_declared_simulator_enums(self):
        listing = self.server.state.job_argv("flow", {"list": True})
        self.assertEqual(listing[listing.index("flow"):], ["flow", "--list", "--json"])
        implicit = self.server.state.job_argv("flow", {})
        self.assertEqual(implicit[implicit.index("flow"):], ["flow", "--list", "--json"])
        for simulator in ("icarus", "verilator", "questa", "vcs", "xcelium"):
            with self.subTest(simulator=simulator):
                named = self.server.state.job_argv("flow", {"flow": "upstream-original", "simulator": simulator})
                self.assertEqual(named[named.index("flow"):], ["flow", "upstream-original", "--simulator", simulator])
        self.assertIn("-I", named)

    def test_project_flow_http_rejects_commands_environment_and_incompatible_listing_options(self):
        self.pair()
        options = [
            {"argv": ["sh", "-c", "anything"]}, {"flow": "native", "environment": {"PATH": "/tmp"}},
            {"flow": "native", "env": {"PYTHONPATH": "/tmp"}}, {"flow": "native", "driver": "python"},
            {"flow": "native", "manifest": "outside.json"}, {"flow": "native", "cwd": "../outside"},
            {"list": True, "flow": "native"}, {"list": True, "simulator": "verilator"},
            {"list": False}, {"list": "yes"}, {"flow": "--help"}, {"flow": "../native"},
            {"flow": "native; touch marker"}, {"flow": "native\n"},
            {"flow": "native", "simulator": "sh"}, {"flow": "native", "simulator": "auto"},
        ]
        for value in options:
            with self.subTest(options=value):
                status, _, _ = self.request("POST", "/v1/jobs", {"command": "flow", "options": value})
                self.assertEqual(status, 400)
        self.assertEqual(self.request("POST", "/v1/jobs", {"command": "flow", "environment": {"PATH": "/tmp"}})[0], 400)
        self.assertEqual(self.server.state.jobs, {})

    def test_project_flow_long_timeout_is_bounded_and_forwarded_to_native_cli(self):
        self.project_flow()
        self.pair()
        for timeout in (True, 0, -1, 7201, "7200"):
            with self.subTest(timeout=timeout):
                self.assertEqual(self.request("POST", "/v1/jobs", {"command": "flow", "options": {"flow": "native"}, "timeout": timeout})[0], 400)
        self.assertEqual(self.request("POST", "/v1/jobs", {"command": "list", "timeout": 301})[0], 400)
        self.assertEqual(self.server.state.jobs, {})
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, "typed timeout adapter exercised", "")

        self.server.state.runner = runner
        status, job, _ = self.request("POST", "/v1/jobs", {"command": "flow", "options": {"flow": "http-upstream"}, "timeout": 7200})
        self.assertEqual(status, 202, job)
        self.assertEqual(self.wait_job(job)["status"], "passed")
        argv, kwargs = calls[0]
        self.assertEqual(kwargs["timeout"], 7200)
        self.assertEqual(argv[-2:], ["--timeout", "7200"])

    def test_real_http_project_flow_uses_declared_environment_and_exposes_fresh_artifacts(self):
        self.project_flow()
        self.pair()
        status, listing, _ = self.request("POST", "/v1/jobs", {"command": "flow", "options": {"list": True}, "timeout": 15})
        self.assertEqual(status, 202, listing)
        listing = self.wait_job(listing)
        self.assertEqual(listing["status"], "passed", listing)
        discovery = json.loads(listing["stdout"])["flows"][0]
        self.assertEqual(discovery["id"], "http-upstream")
        self.assertEqual(discovery["availability"]["missing_python_modules"], [])
        status, job, _ = self.request("POST", "/v1/jobs", {"command": "flow", "options": {"flow": "http-upstream"}, "timeout": 15})
        self.assertEqual(status, 202, job)
        job = self.wait_job(job)
        self.assertEqual(job["status"], "passed", job)
        self.assertEqual(job["returncode"], 0)
        self.assertFalse(job["stale"], job)
        self.assertIn("ORIGINAL_PROJECT_BENCH_FINISHED", job["stdout"])
        status, artifacts, _ = self.request("GET", "/v1/artifacts")
        self.assertEqual(status, 200, artifacts)
        names = {item["path"] for item in artifacts["files"]}
        base = ".vwb/flows/http-upstream/"
        self.assertTrue({base + "report.json", base + "run.log", base + "results/native-results.xml"}.issubset(names), names)
        status, payload, _ = self.request("GET", "/v1/artifacts/file?path=" + quote(base + "report.json"))
        self.assertEqual(status, 200)
        report = json.loads(payload) if isinstance(payload, bytes) else payload
        self.assertEqual(report["state"], "passed")
        self.assertEqual(report["verdict"]["tests"], 2)
        status, result, _ = self.request("GET", "/v1/artifacts/file?path=" + quote(base + "results/native-results.xml"))
        self.assertEqual(status, 200)
        self.assertEqual(result, (self.root / "native-results.xml").read_bytes())

    def test_real_http_project_flow_zero_exit_does_not_mask_junit_failure(self):
        self.project_flow(failing=True)
        self.pair()
        status, job, _ = self.request("POST", "/v1/jobs", {"command": "flow", "options": {"flow": "http-upstream"}, "timeout": 15})
        self.assertEqual(status, 202, job)
        job = self.wait_job(job)
        self.assertEqual(job["status"], "failed", job)
        status, payload, _ = self.request("GET", "/v1/artifacts/file?path=.vwb/flows/http-upstream/report.json")
        self.assertEqual(status, 200)
        report = json.loads(payload) if isinstance(payload, bytes) else payload
        self.assertEqual(report["steps"][0]["returncode"], 0)
        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["verdict"]["failures"], 1)

    def test_real_http_project_flow_missing_tool_stays_blocked(self):
        flow = self.project_flow()
        flow["requirements"]["tools"].append("vwb_missing_native_simulator")
        flow["requirements"]["licenses"] = ["Simulator licence requires actual execution to verify"]
        (self.root / ".vwb-flows.json").write_text(json.dumps({"schema_version": 1, "flows": [flow]}))
        self.pair()
        status, job, _ = self.request("POST", "/v1/jobs", {"command": "flow", "options": {"flow": "http-upstream"}, "timeout": 15})
        self.assertEqual(status, 202, job)
        job = self.wait_job(job)
        self.assertEqual(job["status"], "failed", job)
        status, payload, _ = self.request("GET", "/v1/artifacts/file?path=.vwb/flows/http-upstream/report.json")
        self.assertEqual(status, 200)
        report = json.loads(payload) if isinstance(payload, bytes) else payload
        self.assertEqual(report["state"], "blocked")
        self.assertIn("vwb_missing_native_simulator", report["dependencies"]["missing_tools"])
        self.assertEqual(report["dependencies"]["license_state"], "not_verified")
        self.assertFalse((self.root / "native-results.xml").exists())

    def test_project_flow_snapshot_marks_root_inputs_or_manifest_edits_stale(self):
        self.project_flow()
        root_bench = self.root / "original_root_bench.py"
        root_bench.write_text("# original upstream root testbench\n")
        path = self.root / ".vwb-flows.json"
        manifest = json.loads(path.read_text())
        manifest["source_files"].append(root_bench.name)
        path.write_text(json.dumps(manifest))
        self.pair()
        for mutation in ("root_bench", "manifest", "deleted_input"):
            with self.subTest(mutation=mutation):
                root_bench.write_text("# original upstream root testbench\n")
                path.write_text(json.dumps(manifest))
                started, finish = threading.Event(), threading.Event()

                def runner(argv, **kwargs):
                    started.set()
                    finish.wait(3)
                    return subprocess.CompletedProcess(argv, 0, "native verification completed", "")

                self.server.state.runner = runner
                status, job, _ = self.request("POST", "/v1/jobs", {"command": "flow", "options": {"flow": "http-upstream"}})
                self.assertEqual(status, 202, job)
                self.assertTrue(started.wait(2))
                if mutation == "root_bench":
                    root_bench.write_text("# changed outside src/test during native verification\n")
                elif mutation == "deleted_input":
                    root_bench.unlink()
                else:
                    path.write_text(json.dumps(manifest) + "\n")
                finish.set()
                current = self.wait_job(job)
                self.assertEqual(current["status"], "passed", current)
                self.assertTrue(current["stale"], current)
                self.assertNotEqual(current["source_revision"], current["source_revision_after"])
                self.assertNotIn("_flow_source_files", current)

    def test_project_flow_snapshot_ignores_generated_files_outside_source_tree(self):
        self.project_flow()
        self.pair()

        def runner(argv, **kwargs):
            (self.root / "generated-model.cpp").write_text("// native generated output\n")
            output = self.root / "outdir/nv_small"
            output.mkdir(parents=True)
            (output / "generated.v").write_text("module generated; endmodule\n")
            return subprocess.CompletedProcess(argv, 0, "native verification completed", "")

        self.server.state.runner = runner
        status, job, _ = self.request("POST", "/v1/jobs", {"command": "flow", "options": {"flow": "http-upstream"}})
        self.assertEqual(status, 202, job)
        current = self.wait_job(job)
        self.assertEqual(current["status"], "passed", current)
        self.assertFalse(current["stale"], current)
        self.assertEqual(current["source_revision"], current["source_revision_after"])

    def test_project_flow_snapshot_inputs_are_confined_regular_and_bounded(self):
        self.project_flow()
        self.pair()
        path = self.root / ".vwb-flows.json"
        manifest = json.loads(path.read_text())
        for source_files in (["../outside"], [".git/config"], [".vwb/results.xml"],
                             ["src"], ["./src/counter.sv"], ["src/missing-%d" % index for index in range(10001)]):
            with self.subTest(source_files=source_files[:2], count=len(source_files)):
                path.write_text(json.dumps(dict(manifest, source_files=source_files)))
                status, _, _ = self.request("POST", "/v1/jobs", {"command": "flow", "options": {"flow": "http-upstream"}})
                self.assertIn(status, (400, 403))
        path.write_text(json.dumps(manifest))
        with patch("companion.projects.MAX_FLOW_SNAPSHOT_BYTES", 8):
            self.assertEqual(self.request("POST", "/v1/jobs", {"command": "flow", "options": {"flow": "http-upstream"}})[0], 413)
        self.assertEqual(self.server.state.jobs, {})

    def test_project_flow_snapshot_rejects_linked_declared_inputs(self):
        self.project_flow()
        path = self.root / ".vwb-flows.json"
        manifest = json.loads(path.read_text())
        linked = self.root / "linked-input.py"
        try:
            linked.symlink_to(self.root / "test/original_project_bench.py")
        except (OSError, NotImplementedError) as error:
            self.skipTest("Symlink creation unavailable: " + str(error))
        manifest["source_files"].append(linked.name)
        path.write_text(json.dumps(manifest))
        self.pair()
        status, _, _ = self.request("POST", "/v1/jobs", {"command": "flow", "options": {"flow": "http-upstream"}})
        self.assertIn(status, (400, 403))
        self.assertEqual(self.server.state.jobs, {})

    def test_nonzero_job_exit_is_failed(self):
        self.pair()
        self.server.state.runner = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 19, "", "tool failure")
        _, job, _ = self.request("POST", "/v1/jobs", {"command": "list"})
        for _ in range(100):
            _, current, _ = self.request("GET", "/v1/jobs/" + job["id"])
            if current["status"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(current["status"], "failed")
        self.assertEqual(current["returncode"], 19)

    def test_cancellation_and_timeout_reported(self):
        self.pair()
        def runner(argv, **kwargs):
            kwargs["cancel_event"].wait(3)
            result = subprocess.CompletedProcess(argv, 130, "", "")
            result.cancelled = True
            return result
        self.server.state.runner = runner
        status, job, _ = self.request("POST", "/v1/jobs", {"command": "list"})
        self.assertEqual(status, 202)
        self.assertEqual(self.request("POST", "/v1/jobs", {"command": "list"})[0], 409)
        self.assertEqual(self.request("POST", "/v1/jobs/" + job["id"] + "/cancel", {})[0], 200)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, current, _ = self.request("GET", "/v1/jobs/" + job["id"])
            if current["status"] != "running":
                break
            time.sleep(0.02)
        self.assertEqual(current["status"], "cancelled")
        def timeout_runner(argv, **kwargs):
            result = subprocess.CompletedProcess(argv, 124, "partial", "timeout")
            result.timed_out = True
            return result
        self.server.state.runner = timeout_runner
        _, job, _ = self.request("POST", "/v1/jobs", {"command": "list", "timeout": 1})
        for _ in range(100):
            _, current, _ = self.request("GET", "/v1/jobs/" + job["id"])
            if current["status"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(current["status"], "timed_out")

    def test_job_source_revision_marks_external_edits_stale(self):
        self.pair()
        started = threading.Event()
        finish = threading.Event()
        def runner(argv, **kwargs):
            started.set()
            finish.wait(3)
            return subprocess.CompletedProcess(argv, 0, "", "")
        self.server.state.runner = runner
        _, job, _ = self.request("POST", "/v1/jobs", {"command": "list"})
        self.assertTrue(started.wait(2))
        (self.root / "src/counter.sv").write_bytes(b"module changed; endmodule\n")
        finish.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _, current, _ = self.request("GET", "/v1/jobs/" + job["id"])
            if current["status"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(current["status"], "passed")
        self.assertTrue(current["stale"])
        self.assertNotEqual(current["source_revision"], current["source_revision_after"])

    @unittest.skipIf(os.name == "nt", "POSIX SIGTERM graceful shutdown; Windows forced termination has different semantics")
    def test_sigterm_host_shutdown_reaps_native_child_and_grandchild(self):
        # Use a real companion main, HTTP pairing/job, and native process tree.
        # The injected runner is trusted test setup, not an HTTP command field.
        pids_path = self.root / "native-pids.json"
        child_script = (
            "import json,os,pathlib,subprocess,sys,time; "
            "grandchild_path=pathlib.Path(sys.argv[1]+'.grandchild'); "
            "grandchild_code='import json,os,pathlib,sys,time; proc=os.readlink(\"/proc/self\") if pathlib.Path(\"/proc/self\").exists() else str(os.getpid()); pathlib.Path(sys.argv[1]).write_text(json.dumps([os.getpid(),proc])); time.sleep(60)'; "
            "child=subprocess.Popen([sys.executable,'-c',grandchild_code,str(grandchild_path)]); "
            "deadline=time.monotonic()+10; "
            "exec('while not grandchild_path.exists() and time.monotonic()<deadline:\\n time.sleep(0.01)'); "
            "grandchild_pid=json.loads(grandchild_path.read_text()); "
            "proc=os.readlink('/proc/self') if pathlib.Path('/proc/self').exists() else str(os.getpid()); "
            "pathlib.Path(sys.argv[1]).write_text(json.dumps([[os.getpid(),proc],grandchild_pid])); "
            "time.sleep(60)"
        )
        bootstrap = (
            "import sys\n"
            "from companion import __main__ as cli\n"
            "from companion.process import run_process\n"
            "original_create = cli.create_server\n"
            f"child_script = {child_script!r}\n"
            f"pids_path = {str(pids_path)!r}\n"
            "def runner(argv, **kwargs):\n"
            "    return run_process([sys.executable, '-c', child_script, pids_path], **kwargs)\n"
            "def create(*args, **kwargs):\n"
            "    kwargs['runner'] = runner\n"
            "    return original_create(*args, **kwargs)\n"
            "cli.create_server = create\n"
            f"raise SystemExit(cli.main(['--project', {str(self.root)!r}, '--origin', {ORIGIN!r}]))\n"
        )
        host = subprocess.Popen([sys.executable, "-c", bootstrap], cwd=Path(__file__).resolve().parents[1],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready_lines = queue.Queue(maxsize=1)
        stderr_chunks = deque(maxlen=16)
        def read_ready():
            ready_lines.put(host.stdout.readline(4097))
        def read_stderr():
            while True:
                chunk = os.read(host.stderr.fileno(), 1024)
                if not chunk:
                    break
                stderr_chunks.append(chunk.decode("utf-8", errors="replace"))
        ready_thread = threading.Thread(target=read_ready, daemon=True)
        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        ready_thread.start()
        stderr_thread.start()
        pids = []
        proc_pids = {}
        def running(pid):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            proc_stat = Path(f"/proc/{proc_pids.get(pid, pid)}/stat")
            if proc_stat.exists():
                return proc_stat.read_text().rsplit(")", 1)[1].split()[0] != "Z"
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
            return bool(state.stdout.strip()) and not state.stdout.lstrip().startswith("Z")
        def native_request(method, path, body, token=None):
            headers = {"Origin": ORIGIN, "Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer " + token
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request(method, path, json.dumps(body), headers)
            response = connection.getresponse()
            data = json.loads(response.read())
            connection.close()
            return response.status, data
        try:
            try:
                line = ready_lines.get(timeout=10)
            except queue.Empty:
                self.fail(f"companion did not become ready; exit={host.poll()}, stderr={''.join(stderr_chunks)[-8192:]!r}")
            self.assertTrue(line, f"companion closed stdout before ready; exit={host.poll()}, stderr={''.join(stderr_chunks)[-8192:]!r}")
            ready = json.loads(line)
            port = int(ready["url"].rsplit(":", 1)[1])
            status, paired = native_request("POST", "/v1/pair", {"code": ready["pairing_code"]})
            self.assertEqual(status, 200)
            status, job = native_request("POST", "/v1/jobs", {"command": "list"}, paired["token"])
            self.assertEqual(status, 202, job)
            deadline = time.monotonic() + 10
            while not pids and time.monotonic() < deadline:
                self.assertIsNone(host.poll(), "companion exited before the native job started")
                try:
                    records = json.loads(pids_path.read_text())
                    pids = [record[0] for record in records]
                    proc_pids.update({record[0]: record[1] for record in records})
                except (FileNotFoundError, ValueError):
                    pass
                time.sleep(0.02)
            self.assertEqual(len(pids), 2, "native process tree did not start")
            self.assertTrue(all(running(pid) for pid in pids))
            host.send_signal(signal.SIGTERM)
            host.wait(timeout=10)
            stderr_thread.join(timeout=2)
            self.assertEqual(host.returncode, 0, "".join(stderr_chunks))
            deadline = time.monotonic() + 3
            while any(running(pid) for pid in pids) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse([pid for pid in pids if running(pid)], "native descendants survived companion SIGTERM")
        finally:
            if host.poll() is None:
                host.kill()
                host.wait(timeout=5)
            for pid in pids:
                if running(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            ready_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            host.stdout.close()
            host.stderr.close()


if __name__ == "__main__":
    unittest.main()
