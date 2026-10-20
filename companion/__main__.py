"""Launch a native VWB project grant. No administrator rights required."""
import argparse
import json
from pathlib import Path
import signal
import sys
import threading

from .projects import ProjectError
from .server import create_server


def main(argv=None):
    parser = argparse.ArgumentParser(description="VWB loopback companion for one trusted local project", allow_abbrev=False)
    parser.add_argument("--project", required=True, help="trusted project folder to grant to the paired browser")
    parser.add_argument("--origin", required=True, help="exact website origin, e.g. https://sfmth.github.io")
    parser.add_argument("--port", type=int, default=0, help="loopback port; 0 chooses an available port")
    parser.add_argument("--src-dir", help="project-relative source directory")
    parser.add_argument("--test-dir", help="project-relative test directory")
    parser.add_argument("--build-dir", help="project-relative generated output directory")
    parser.add_argument('--editor-config', type=Path, help='trusted local language-server JSON config (commands cannot be supplied by the browser)')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be from 0 to 65535")
    try:
        editor_config = None
        if args.editor_config is not None:
            with args.editor_config.open('rb') as source:
                raw = source.read(65537)
            if len(raw) > 65536:
                raise ValueError('editor config exceeds 64 KiB')
            editor_config = json.loads(raw)
        server = create_server(args.project, args.origin, port=args.port, src_dir=args.src_dir,
                               test_dir=args.test_dir, build_dir=args.build_dir, editor_config=editor_config)
    except (OSError, ValueError, ProjectError) as exc:
        print(f"Cannot start companion: {exc}", file=sys.stderr)
        return 2
    previous_sigterm = None
    owns_signal_handler = threading.current_thread() is threading.main_thread()
    def terminate_as_interrupt(_signum, _frame):
        # Native jobs own separate process trees. Unwind through server_close
        # so cancellation reaches those trees before the companion exits.
        raise KeyboardInterrupt
    if owns_signal_handler:
        previous_sigterm = signal.signal(signal.SIGTERM, terminate_as_interrupt)
    try:
        print(json.dumps({"event": "ready", "url": f"http://127.0.0.1:{server.server_port}",
                          "pairing_code": server.state.pairing_code, "expires_in": 300}), flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.server_close()
        finally:
            if owns_signal_handler:
                signal.signal(signal.SIGTERM, previous_sigterm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
