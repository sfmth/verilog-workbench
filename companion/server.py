"""Small authenticated HTTP API; intentionally no general command endpoint."""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import sys
from socketserver import TCPServer
import threading
import time
from urllib.parse import parse_qs, urlsplit

from . import PROTOCOL_VERSION, VERSION
from .projects import MAX_FILE_BYTES, ProjectError, ProjectGrant

MAX_BODY_BYTES = 3 * 1024 * 1024
MAX_EDITOR_BODY_BYTES = 12 * 1024 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_JOB_SECONDS = 300
MAX_FLOW_SECONDS = 7200
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*\Z")
SIMULATION_OPTIONS = {"modules", "simulator", "test", "test_language", "test_top", "testcase", "waves", "wave_format", "seed",
                      "max_array_words", "defines", "includes", "plusargs", "gate_level", "keep_going"}
COMMAND_OPTIONS = {
    "list": set(), "doctor": set(),
    "test": SIMULATION_OPTIONS,
    "wave": SIMULATION_OPTIONS | {"tag", "replace_tag", "load", "list_saved"},
    "lint": {"modules", "all", "linters", "defines", "includes", "keep_going"},
    "synth": {"module", "format", "full", "flatten", "schematic", "defines", "includes"},
    "formal": {"config"},
    "fpga": {"module", "board", "stage", "constraints", "program", "defines", "includes"},
    "flow": {"flow", "list", "simulator"},
}


def validate_origin(origin):
    parsed = urlsplit(origin)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("origin must be an HTTP(S) origin")
    if parsed.path or parsed.query or parsed.fragment or origin != f"{parsed.scheme}://{parsed.netloc}":
        raise ValueError("origin must contain scheme and host only, without a trailing slash")
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("non-loopback website origin must use HTTPS")
    return origin


class CompanionState:
    def __init__(self, project, origin, *, pairing_lifetime=300, runner=None, editor_config=None):
        self.project = project
        self.origin = validate_origin(origin)
        self.pairing_code = secrets.token_urlsafe(18)
        self.pairing_expires_at = time.monotonic() + pairing_lifetime
        self.pairing_attempts = 0
        self.token = None
        self.lock = threading.RLock()
        self.jobs = {}
        self.capabilities = None
        self.runner = runner
        from .editor import EditorService
        from .edits import EditTransactions
        from .files import FileOperations
        self.editor = EditorService(project, editor_config)
        self.edits = EditTransactions(project)
        self.files = FileOperations(project)
        self.editor_busy = False
        self.editor_plans = {}
        self.editor_undos = {}

    def editor_capabilities(self):
        result = self.editor.capabilities()
        result['transactions'] = self.edits.status()
        return result

    def file_operation(self, operation, payload):
        with self.lock, self.project.lock:
            self._editor_idle()
            result = getattr(self.files, operation)(payload)
            # A path change invalidates service previews and inverse edit IDs.
            self.editor_plans.clear()
            self.editor_undos.clear()
            return result

    def editor_query(self, payload):
        if not isinstance(payload, dict):
            raise ProjectError(400, 'editor query must be an object')
        rename = payload.get('operation') == 'rename'
        if self.edits.status()['recovery_required']:
            raise ProjectError(409, 'recover the interrupted editor transaction before semantic queries')
        if rename:
            with self.lock:
                self._editor_idle()
                self.editor_busy = True
        try:
            response = self.editor.query(payload)
            if rename:
                with self.lock:
                    if (self.project.source_revision() != response['source_revision'] or
                            self.editor.project_fingerprint() != response['project_fingerprint']):
                        raise ProjectError(409, 'project changed during rename; request a new preview')
                    plan = self.edits.preview(response['result']['files'])
                    if len(self.editor_plans) >= 16:
                        self.editor_plans.pop(next(iter(self.editor_plans)))
                    self.editor_plans[plan['id']] = (response['source_revision'], response['project_fingerprint'])
                return {'operation': 'rename', 'plan': plan, 'language': response['language'],
                        'path': response['path'], 'version': response['version'], 'revision': response['revision'],
                        'source_revision': response['source_revision']}
            return response
        finally:
            if rename:
                with self.lock:
                    self.editor_busy = False

    def _editor_idle(self, *, recovery=False):
        if self.editor_busy or any(job['status'] == 'running' for job in self.jobs.values()):
            raise ProjectError(409, 'stop the active job or editor operation before changing project sources')
        if not recovery and self.edits.status()['recovery_required']:
            raise ProjectError(409, 'an interrupted editor transaction requires recovery first')

    def editor_edit(self, operation, payload):
        if not isinstance(payload, dict) or (set(payload) != {'id'} if operation != 'recover' else bool(payload)):
            raise ProjectError(400, 'editor operation requires only its plan id, or an empty recovery object')
        with self.lock:
            self._editor_idle(recovery=operation == 'recover')
            if operation != 'recover':
                key = payload['id']
                if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', key):
                    raise ProjectError(400, 'invalid editor plan id')
                known = self.editor_plans if operation == 'apply' else self.editor_undos
                current = (self.project.source_revision(), self.editor.project_fingerprint())
                if key not in known or current != known[key]:
                    raise ProjectError(409, 'project changed or edit plan expired; request a fresh preview')
            if operation == 'apply':
                result = self.edits.apply(key)
                self.editor_plans.pop(key, None)
                self.editor_undos.clear()
                self.editor_undos[result['undo_id']] = (self.project.source_revision(), self.editor.project_fingerprint())
            elif operation == 'undo':
                result = self.edits.undo(key)
                self.editor_undos.pop(key, None)
            else:
                result = self.edits.recover()
                self.editor_plans.clear(); self.editor_undos.clear()
            return result

    def pair(self, code):
        with self.lock:
            if self.token is not None:
                raise ProjectError(409, "pairing code has already been used; restart companion to pair again")
            if time.monotonic() >= self.pairing_expires_at:
                raise ProjectError(410, "pairing code expired; restart companion")
            if self.pairing_attempts >= 5:
                raise ProjectError(429, "too many pairing attempts; restart companion")
            self.pairing_attempts += 1
            if not isinstance(code, str) or not hmac.compare_digest(code.encode("utf-8"), self.pairing_code.encode("ascii")):
                raise ProjectError(401, "invalid pairing code")
            self.token = secrets.token_urlsafe(32)
            return {"token": self.token, "project": self.project.summary(), "protocol": PROTOCOL_VERSION}

    def authorize(self, credential):
        if self.token is None or not isinstance(credential, str) or not hmac.compare_digest(credential.encode("utf-8"), ("Bearer " + self.token).encode("ascii")):
            raise ProjectError(401, "valid bearer session token required")

    def job_argv(self, command, options):
        if command not in COMMAND_OPTIONS or not isinstance(options, dict):
            raise ProjectError(400, "unknown command or invalid options object")
        if set(options) - COMMAND_OPTIONS[command]:
            raise ProjectError(400, "unsupported command option")
        project = self.project
        for setting in (project.src_dir, project.test_dir, project.build_dir):
            project.path(setting, allow_root=True)
        # All paths come from the launch grant. These explicit flags override
        # later edits to .vwb.json and prevent a browser changing the build root.
        from pathlib import Path
        application = Path(__file__).resolve().parents[1]
        driver = application / "vwb.py"
        launcher = application / "_launch.py"
        if launcher.is_file():
            argv = [sys.executable, "-I", str(launcher), "vwb"]
        else:
            # Python -I ignores user-site/PYTHONPATH/cwd imports. Add only the
            # exact trusted source checkout so vwb can import companion.process.
            bootstrap = ("import runpy,sys; app,driver=sys.argv[1:3]; "
                         "sys.path.insert(0,app); sys.argv=sys.argv[2:]; "
                         "runpy.run_path(driver,run_name='__main__')")
            argv = [sys.executable, "-I", "-c", bootstrap, str(application), str(driver)]
        argv += ["--root", str(project.root), "--src-dir", project.src_dir,
                "--test-dir", project.test_dir, "--build-dir", project.build_dir, "--color", "never"]
        management = command == 'wave' and (options.get('list_saved') is True or 'load' in options)
        if command not in ('list', 'doctor') and not management:
            # The browser's Output panel is the native terminal for this job.
            # Retain passing simulator output as well as compiler failures.
            argv.append('--verbose')
        argv.append(command)
        def enum(key, values, flag=None):
            if key in options:
                value = options[key]
                if not isinstance(value, str) or value not in values:
                    raise ProjectError(400, f"invalid {key}")
                argv.extend([flag or "--" + key.replace("_", "-"), value])
        def boolean(key, flag, negative=None):
            if key in options:
                if type(options[key]) is not bool:
                    raise ProjectError(400, f"{key} must be boolean")
                if options[key]:
                    argv.append(flag)
                elif negative:
                    argv.append(negative)
        def identifier(value):
            if not isinstance(value, str) or len(value) > 128 or not IDENTIFIER.fullmatch(value):
                raise ProjectError(400, "module must be an ordinary HDL identifier")
            return value
        def integer(key, maximum):
            if key in options:
                value = options[key]
                if type(value) is not int or not 0 <= value <= maximum:
                    raise ProjectError(400, f"{key} must be an integer from 0 to {maximum}")
                argv.extend(["--" + key.replace("_", "-"), str(value)])
        def strings(key):
            values = options[key]
            if not isinstance(values, list) or len(values) > 32:
                raise ProjectError(400, f"{key} must be a list of at most 32 strings")
            for value in values:
                if not isinstance(value, str) or not value or len(value) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in value):
                    raise ProjectError(400, f"invalid {key} value")
                try:
                    value.encode('utf-8')
                except UnicodeError as exc:
                    raise ProjectError(400, f"invalid UTF-8 {key} value") from exc
            return values
        def source_path(value, *, directory=False):
            path = project.path(value, allow_root=directory)
            if path == project.build_path or project.build_path in path.parents:
                raise ProjectError(403, 'HDL inputs must be outside the generated build directory')
            if not (path.is_dir() if directory else path.is_file()):
                raise ProjectError(400, 'HDL input must be an existing project ' + ('directory' if directory else 'file'))
            return path
        def tag(key):
            value = options[key]
            if not isinstance(value, str) or len(value) > 128 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', value):
                raise ProjectError(400, f'{key} must be a portable saved-wave tag of at most 128 characters')
            project.path(project.build_dir + '/saved-waves/' + value)
            argv.extend(['--' + key.replace('_', '-'), value])
        if "module" in options:
            argv.append(identifier(options["module"]))
        if "modules" in options:
            values = options["modules"]
            if not isinstance(values, list) or len(values) > 32:
                raise ProjectError(400, "modules must be a list of at most 32 identifiers")
            argv.extend(identifier(value) for value in values)
        if command in ('test', 'wave', 'lint', 'synth', 'fpga'):
            if 'defines' in options:
                for value in strings('defines'):
                    if not IDENTIFIER.fullmatch(value.split('=', 1)[0]) or len(value.split('=', 1)[0]) > 128:
                        raise ProjectError(400, 'defines must use NAME or NAME=VALUE')
                    # Equals spelling keeps even a value beginning '-' inside
                    # its single typed option; it cannot become a driver flag.
                    argv.append('--define=' + value)
            if 'includes' in options:
                for value in strings('includes'):
                    argv.append('--include=' + str(source_path(value, directory=True)))
        if command == 'flow':
            from .flows import FLOW_ID, SIMULATORS
            listing = options.get('list', not options.get('flow'))
            if type(listing) is not bool:
                raise ProjectError(400, 'flow list must be boolean')
            if listing:
                if set(options) - {'list'}:
                    raise ProjectError(400, 'listing project flows accepts no execution options')
                argv.extend(['--list', '--json'])
            else:
                value = options.get('flow')
                if not isinstance(value, str) or not FLOW_ID.fullmatch(value):
                    raise ProjectError(400, 'choose a named project verification flow')
                argv.append(value)
                enum('simulator', SIMULATORS)
        elif command in ("list", "doctor"):
            argv.append("--json")
        elif command in ('test', 'wave'):
            management = command == 'wave' and (options.get('list_saved') is True or 'load' in options)
            if command == 'wave':
                argv.append('--no-view')
                if 'list_saved' in options and type(options['list_saved']) is not bool:
                    raise ProjectError(400, 'list_saved must be boolean')
                if options.get('list_saved') is True:
                    if set(options) - {'modules', 'list_saved'}:
                        raise ProjectError(400, 'list_saved cannot be combined with simulation or archive options')
                    argv.extend(['--list-saved', '--json'])
                elif 'load' in options:
                    if set(options) - {'modules', 'load'} or len(options.get('modules', [])) > 1:
                        raise ProjectError(400, 'load accepts only one optional module filter')
                    tag('load')
                    argv.append('--json')
                else:
                    if 'tag' in options:
                        tag('tag')
                    boolean('replace_tag', '--replace-tag')
                    if options.get('replace_tag') and 'tag' not in options:
                        raise ProjectError(400, 'replace_tag requires tag')
                    if options.get('waves') is False:
                        raise ProjectError(400, 'wave always captures a waveform')
            if management:
                return argv
            enum("test_language", {"auto", "cocotb", "verilog", "vhdl"})
            enum("simulator", {"auto", "icarus", "verilator", "ghdl"})
            enum("wave_format", {"vcd", "fst"})
            boolean("waves", "--waves")
            boolean('gate_level', '--gate-level')
            boolean('keep_going', '--keep-going')
            integer('seed', 2**32 - 1)
            integer('max_array_words', 1000000)
            detected = None
            if 'test' in options:
                path = source_path(options['test'])
                detected = {'.py': 'cocotb', '.v': 'verilog', '.sv': 'verilog', '.vhd': 'vhdl', '.vhdl': 'vhdl'}.get(path.suffix.lower())
                if detected is None:
                    raise ProjectError(400, 'test must be a Python, Verilog, SystemVerilog or VHDL test file')
                if len(options.get('modules', [])) > 1:
                    raise ProjectError(400, 'an explicit test accepts at most one DUT module')
                if options.get('test_language', 'auto') not in ('auto', detected):
                    raise ProjectError(400, 'test_language does not match the selected test file')
                argv.extend(['--test', str(path)])
            if 'test_top' in options:
                if detected == 'cocotb' or options.get('test_language') == 'cocotb':
                    raise ProjectError(400, 'test_top only applies to HDL testbenches')
                argv.extend(['--test-top', identifier(options['test_top'])])
            if 'testcase' in options:
                value = options['testcase']
                if not isinstance(value, str) or len(value) > 128 or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
                    raise ProjectError(400, 'testcase must be one ordinary Python test function name')
                argv.extend(['--testcase', value])
            if ('testcase' in options or 'seed' in options) and (detected in ('verilog', 'vhdl') or options.get('test_language') in ('verilog', 'vhdl')):
                raise ProjectError(400, 'testcase and seed only apply to Cocotb tests')
            if 'plusargs' in options:
                for value in strings('plusargs'):
                    if not re.fullmatch(r'\+[A-Za-z_][A-Za-z0-9_]*(?:=.*)?', value):
                        raise ProjectError(400, 'plusargs must use +NAME or +NAME=VALUE')
                    argv.append('--plusarg=' + value)
        elif command == "lint":
            boolean("all", "--all")
            boolean('keep_going', '--keep-going')
            if options.get('all') and options.get('modules'):
                raise ProjectError(400, 'choose modules or all, not both')
            if "linters" in options:
                values = options["linters"]
                if not isinstance(values, list) or len(values) > 6:
                    raise ProjectError(400, "linters must be a list")
                for value in values:
                    if not isinstance(value, str) or value not in {"iverilog", "verilator", "yosys", "verible", "ghdl", "all"}:
                        raise ProjectError(400, "invalid linter")
                    argv.extend(["--linter", value])
        elif command == "synth":
            enum("format", {"json", "svg", "png", "dot"})
            boolean("full", "--full")
            boolean("flatten", "--flatten")
            boolean("schematic", "--schematic", "--no-schematic")
            argv.append("--no-view")
        elif command == "formal":
            if "config" in options:
                path = project.path(options["config"])
                if path.suffix != ".sby" or not path.is_file():
                    raise ProjectError(400, "formal config must be a project .sby file")
                argv.append(str(path))
        elif command == "fpga":
            if "board" not in options:
                raise ProjectError(400, "FPGA board is required")
            enum("board", {"tangnano9k", "icebreaker"})
            enum("stage", {"synth", "pnr", "pack", "flash"})
            if "program" in options and type(options["program"]) is not bool:
                raise ProjectError(400, "program must be boolean")
            if options.get("stage") == "flash" and options.get("program") is not True:
                raise ProjectError(400, "FPGA flash requires explicit program: true")
            if options.get("program") is True and options.get("stage") != "flash":
                raise ProjectError(400, "program: true requires flash stage")
            if "constraints" in options:
                path = project.path(options["constraints"])
                if path.suffix not in {".pcf", ".cst"} or not path.is_file():
                    raise ProjectError(400, "constraints must be a project .pcf or .cst file")
                argv.extend(["--constraints", str(path)])
        return argv

    def start_job(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"command", "options", "timeout"}:
            raise ProjectError(400, "invalid job object")
        command = payload.get("command")
        if not isinstance(command, str):
            raise ProjectError(400, "command must be a string")
        seconds = payload.get("timeout", MAX_JOB_SECONDS)
        maximum = MAX_FLOW_SECONDS if command == 'flow' else MAX_JOB_SECONDS
        if type(seconds) is not int or not 1 <= seconds <= maximum:
            raise ProjectError(400, f"timeout must be an integer from 1 to {maximum} seconds")
        argv = self.job_argv(command, payload.get("options", {}))
        if command == 'flow':
            argv.extend(['--timeout', str(seconds)])
        with self.lock:
            if self.editor_busy or self.edits.status()['recovery_required']:
                raise ProjectError(409, 'finish editor refactoring or recovery before starting a native job')
            if any(job["status"] == "running" for job in self.jobs.values()):
                raise ProjectError(409, "a project job is already running")
            if len(self.jobs) >= 32:
                oldest = next(iter(self.jobs))
                del self.jobs[oldest]
            job_id = secrets.token_hex(12)
            flow_source_files = None
            if command == 'flow':
                from .flows import load_flows
                try:
                    flow_source_files = load_flows(self.project.root).get('source_files')
                except (OSError, ValueError) as exc:
                    raise ProjectError(400, 'invalid project verification flow manifest') from exc
                source_revision = self.project.flow_source_revision(flow_source_files)
            else:
                source_revision = self.project.source_revision()
            job = {"id": job_id, "command": command, "status": "running", "returncode": None,
                   "stdout": "", "stderr": "", "cancel_event": threading.Event(),
                   "source_revision": source_revision, "source_revision_after": None, "stale": False}
            if command == 'flow':
                job['_flow_source_files'] = tuple(flow_source_files) if flow_source_files is not None else None
            self.jobs[job_id] = job
            thread = threading.Thread(target=self._run_job, args=(job, argv, seconds), daemon=True)
            job["thread"] = thread
            thread.start()
            return self.job_result(job_id)

    def _run_job(self, job, argv, seconds):
        try:
            if self.runner is None:
                from .process import run_process
                runner = run_process
            else:
                runner = self.runner
            result = runner(argv, cwd=self.project.root, capture=True, timeout=seconds,
                            cancel_event=job["cancel_event"], max_output_bytes=1024 * 1024)
            try:
                source_revision_after = (self.project.flow_source_revision(job.get('_flow_source_files'))
                                         if job['command'] == 'flow' else self.project.source_revision())
                snapshot_error = None
            except (OSError, ProjectError) as exc:
                source_revision_after = None
                snapshot_error = str(exc)
            with self.lock:
                job.update(returncode=result.returncode, stdout=result.stdout or "", stderr=result.stderr or "",
                           status="cancelled" if getattr(result, "cancelled", False) else
                                  "timed_out" if getattr(result, "timed_out", False) else
                                  "passed" if result.returncode == 0 else "failed",
                           source_revision_after=source_revision_after,
                           stale=source_revision_after != job["source_revision"],
                           stdout_truncated=getattr(result, "stdout_truncated", False),
                           stderr_truncated=getattr(result, "stderr_truncated", False))
                if snapshot_error:
                    job["source_snapshot_error"] = snapshot_error
        except Exception as exc:
            with self.lock:
                job.update(status="failed", returncode=1, stderr=f"Cannot run VWB: {type(exc).__name__}: {exc}")

    def job_result(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ProjectError(404, "job not found")
            return {key: value for key, value in self.jobs[job_id].items()
                    if key not in {"cancel_event", "thread", "_flow_source_files"}}

    def cancel_job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ProjectError(404, "job not found")
            self.jobs[job_id]["cancel_event"].set()
            return self.job_result(job_id)

    def close(self):
        with self.lock:
            for job in self.jobs.values():
                job["cancel_event"].set()
            threads = [job["thread"] for job in self.jobs.values()]
        for thread in threads:
            thread.join(timeout=5)
        self.editor.close()


class CompanionHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    def server_bind(self):
        # HTTPServer's implementation reverse-resolves the bind address using
        # getfqdn. A numeric loopback service needs no DNS, and cold resolver
        # delays must not hold native startup hostage (notably on macOS).
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def server_close(self):
        self.state.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server_version = "VWBCompanion/" + VERSION
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        # Credentials, codes, design contents, and URL queries never enter logs.
        pass

    @property
    def state(self):
        return self.server.state

    def _guards(self):
        self.connection.settimeout(10)
        expected = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get_all("Host", []) != [expected]:
            raise ProjectError(403, "unexpected loopback Host")
        origins = self.headers.get_all("Origin", [])
        # Permit credential-free command-line liveness checks only.
        if self.command == "GET" and self.path == "/v1/health" and not origins:
            return
        if origins != [self.state.origin]:
            raise ProjectError(403, "website Origin is not authorized")

    def _send(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Vary", "Origin")
        if self.headers.get_all("Origin", []) == [self.state.origin]:
            self.send_header("Access-Control-Allow-Origin", self.state.origin)
        self.end_headers()
        self.wfile.write(raw)

    def _body(self, limit=MAX_BODY_BYTES):
        if self.headers.get("Transfer-Encoding"):
            raise ProjectError(400, "transfer encoding is not supported")
        if self.headers.get_content_type() != "application/json":
            raise ProjectError(415, "application/json is required")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit():
            raise ProjectError(411, "one Content-Length is required")
        length = int(lengths[0])
        if not 0 < length <= limit:
            raise ProjectError(413, "body exceeds request limit")
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ProjectError(400, "incomplete request body")
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ProjectError(400, "invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise ProjectError(400, "JSON body must be an object")
        return payload

    def _path_parameter(self, query):
        values = parse_qs(query, keep_blank_values=True)
        if set(values) != {"path"} or len(values["path"]) != 1:
            raise ProjectError(400, "one project-relative path is required")
        return values["path"][0]

    def do_OPTIONS(self):
        try:
            self._guards()
            method = self.headers.get("Access-Control-Request-Method")
            requested = {s.strip().lower() for s in self.headers.get("Access-Control-Request-Headers", "").split(",") if s.strip()}
            if method not in {"GET", "POST", "PUT"} or not requested <= {"authorization", "content-type"}:
                raise ProjectError(403, "preflight is not allowed")
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.state.origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Vary", "Origin")
            # Older browser PNA preflight; newer LNA may also ask the user for
            # local-network permission. This header never grants API access.
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", "0")
            self.end_headers()
        except ProjectError as exc:
            self._send(exc.status, {"error": str(exc)})

    def _dispatch(self):
        self._guards()
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise ProjectError(400, "invalid request target")
        path = parsed.path
        if self.command == "GET" and path == "/v1/health" and not parsed.query:
            return 200, {"status": "ok", "protocol": PROTOCOL_VERSION, "version": VERSION}
        if self.command == "POST" and path == "/v1/pair" and not parsed.query:
            payload = self._body()
            if set(payload) != {"code"}:
                raise ProjectError(400, "pair body must contain only code")
            return 200, self.state.pair(payload["code"])
        if len(self.headers.get_all("Authorization", [])) != 1:
            raise ProjectError(401, "one bearer session token required")
        self.state.authorize(self.headers.get("Authorization"))
        if self.command == 'GET' and path == '/v1/project/entry':
            return 200, self.state.files.entry(self._path_parameter(parsed.query))
        if self.command == 'GET' and path == '/v1/project/trash' and not parsed.query:
            return 200, self.state.files.trash_list()
        if self.command == 'POST' and path in {
                '/v1/project/create', '/v1/project/move', '/v1/project/trash', '/v1/project/restore'} and not parsed.query:
            return 200, self.state.file_operation(path.rsplit('/', 1)[1], self._body())
        if self.command == 'GET' and path == '/v1/editor/capabilities' and not parsed.query:
            return 200, self.state.editor_capabilities()
        if self.command == 'POST' and path == '/v1/editor/query' and not parsed.query:
            return 200, self.state.editor_query(self._body(MAX_EDITOR_BODY_BYTES))
        if self.command == 'POST' and path in {'/v1/editor/apply', '/v1/editor/undo', '/v1/editor/recover'} and not parsed.query:
            return 200, self.state.editor_edit(path.rsplit('/', 1)[1], self._body())
        if self.command == "GET" and path == "/v1/capabilities" and not parsed.query:
            with self.state.lock:
                if self.state.capabilities is None:
                    from .toolchains import capability_report
                    self.state.capabilities = capability_report()
            return 200, {"protocol": PROTOCOL_VERSION, "project": self.state.project.summary(),
                         "commands": sorted(COMMAND_OPTIONS), "toolchains": self.state.capabilities,
                         "limits": {"file_bytes": MAX_FILE_BYTES, "request_bytes": MAX_BODY_BYTES,
                                    "job_seconds": MAX_JOB_SECONDS, "project_flow_seconds": MAX_FLOW_SECONDS}, "execution": "trusted-local-project"}
        if self.command == "GET" and path == "/v1/project/files" and not parsed.query:
            return 200, self.state.project.inventory()
        if path == "/v1/project/file":
            if self.command == "GET":
                return 200, self.state.project.read(self._path_parameter(parsed.query))
            if self.command == "PUT" and not parsed.query:
                payload = self._body()
                if set(payload) != {"path", "content", "revision"}:
                    raise ProjectError(400, "save requires path, content, and revision")
                with self.state.lock:
                    if self.state.editor_busy or self.state.edits.status()['recovery_required']:
                        raise ProjectError(409, 'finish editor refactoring or recovery before saving project files')
                    return 200, self.state.project.save(payload["path"], payload["content"], payload["revision"])
        if self.command == "GET" and path == "/v1/artifacts" and not parsed.query:
            return 200, self.state.project.inventory(artifacts=True)
        if self.command == "GET" and path == "/v1/artifacts/file":
            value = self._path_parameter(parsed.query)
            target = self.state.project.path(value)
            if self.state.project.private_editor_path(target):
                raise ProjectError(403, 'editor recovery data is private')
            if self.state.project.build_path not in target.parents or not target.is_file():
                raise ProjectError(403, "artifact must be inside the project build directory")
            with target.open("rb") as handle:
                data = handle.read(MAX_ARTIFACT_BYTES + 1)
            if len(data) > MAX_ARTIFACT_BYTES:
                raise ProjectError(413, "artifact exceeds 32 MiB download limit")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", "attachment")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Origin", self.state.origin)
            self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(data)
            return None
        if self.command == "POST" and path == "/v1/jobs" and not parsed.query:
            return 202, self.state.start_job(self._body())
        match = re.fullmatch(r"/v1/jobs/([a-f0-9]{24})(/cancel)?", path)
        if match and not parsed.query:
            if self.command == "GET" and not match[2]:
                return 200, self.state.job_result(match[1])
            if self.command == "POST" and match[2]:
                if self._body():
                    raise ProjectError(400, "cancel requires an empty JSON object")
                return 200, self.state.cancel_job(match[1])
        raise ProjectError(404, "endpoint not found")

    def _handle(self):
        try:
            result = self._dispatch()
            if result is not None:
                self._send(*result)
        except ProjectError as exc:
            self._send(exc.status, {"error": str(exc)})
        except (OSError, ValueError):
            self._send(400, {"error": "request could not be completed"})
        except Exception:
            self._send(500, {"error": "internal companion error"})

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle


def create_server(project, origin, *, port=0, src_dir=None, test_dir=None, build_dir=None,
                  pairing_lifetime=300, runner=None, editor_config=None):
    grant = project if isinstance(project, ProjectGrant) else ProjectGrant(
        project, src_dir=src_dir, test_dir=test_dir, build_dir=build_dir)
    state = CompanionState(grant, origin, pairing_lifetime=pairing_lifetime, runner=runner, editor_config=editor_config)
    server = CompanionHTTPServer(("127.0.0.1", port), Handler)
    server.state = state
    return server
