#!/usr/bin/env python3
"""Exercise a relocated native bundle; emit no pairing/session credentials."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request


def smoke(bundle):
    bundle = Path(bundle).resolve()
    target = json.loads((bundle / 'bundle.json').read_text(encoding='utf-8'))['target']
    windows = os.name == 'nt'
    python = bundle / ('runtime/python.exe' if windows else 'runtime/bin/python3')
    launch = bundle / 'app/_launch.py'
    origin = 'https://sfmth.github.io'
    environment = dict(os.environ, PYTHONHOME='/nonexistent-pythonhome', PYTHONPATH='/nonexistent-pythonpath')
    with tempfile.TemporaryDirectory(prefix='vwb-native-proof-') as temporary:
        project = Path(temporary) / 'project with spaces µ'
        (project / 'src').mkdir(parents=True)
        (project / 'test').mkdir()
        # Use explicit CRLF on every host so the proof checks byte-preserving
        # saves rather than the platform's text-mode newline translation.
        (project / 'src/top.v').write_bytes(b'module top; endmodule\r\n')
        (project / 'test/__init__.py').write_text('', encoding='utf-8')
        empty = Path(temporary) / 'empty-cwd'
        empty.mkdir()
        version = subprocess.run([str(python), '-I', str(launch), 'vwb', '--version'],
                                 cwd=empty, env=environment, capture_output=True, text=True, timeout=20)
        if version.returncode:
            raise RuntimeError('Bundled VWB version command failed')
        process = subprocess.Popen([str(python), '-I', str(launch), 'companion', '--project', str(project),
                                    '--origin', origin, '--port', '0'], cwd=empty, env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        lines = queue.Queue()
        reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            ready = json.loads(lines.get(timeout=20))
            base = ready['url']
            token = None
            def request(path, body=None, method=None):
                headers = {'Origin':origin}
                if token:
                    headers['Authorization'] = 'Bearer ' + token
                if body is not None:
                    headers['Content-Type'] = 'application/json'
                req = urllib.request.Request(base + path,
                    data=None if body is None else json.dumps(body).encode(), headers=headers,
                    method=method or ('POST' if body is not None else 'GET'))
                with urllib.request.urlopen(req, timeout=15) as response:
                    return json.load(response)
            health = request('/v1/health')
            assert health['protocol'] == 1
            paired = request('/v1/pair', {'code':ready['pairing_code']})
            token = paired['token']
            files = request('/v1/project/files')
            before = request('/v1/project/file?path=src%2Ftop.v')
            changed = before['content'] + '// saved through native proof\n'
            request('/v1/project/file', {'path':'src/top.v','content':changed,'revision':before['revision']}, method='PUT')
            assert (project / 'src/top.v').read_bytes() == changed.encode('utf-8')
            job = request('/v1/jobs', {'command':'list', 'options':{}, 'timeout':30})
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                result = request('/v1/jobs/' + job['id'])
                if result['status'] != 'running':
                    break
                time.sleep(.1)
            assert result['status'] == 'passed', {k:result.get(k) for k in ['status','returncode','stderr']}
            inventory = json.loads(result['stdout'])
            assert any(module['name'] == 'top' for module in inventory['modules'])
            return {'schema':'vwb.native-smoke/1','target':target,'passed':True,
                    'runtime':'bundled interpreter under isolated -I startup',
                    'checks':['empty-cwd','poisoned-Python-environment','version','native-startup',
                              'pairing','local-folder-inventory','revision-checked-save','real-vwb-job'],
                    'toolchains_included':False, 'hardware_tested':False}
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            for pipe in (process.stdout, process.stderr):
                pipe.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--output', type=Path)
    args=parser.parse_args()
    result=smoke(args.bundle)
    content=json.dumps(result, indent=2)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding='utf-8')
    print(content, end='')


if __name__=='__main__':
    main()
