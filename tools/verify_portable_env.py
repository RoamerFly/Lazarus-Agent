"""Verify a portable EXE and env without developer PATH or saved configuration."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def verify(folder, receipt):
    folder = folder.resolve()
    errors = []
    manifest = json.loads((folder/'env/manifest.json').read_text(encoding='utf-8'))
    for item in manifest['files']:
        path = folder/'env'/item['path']
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b''):
                digest.update(chunk)
        if path.stat().st_size != item['bytes'] or digest.hexdigest() != item['sha256']:
            errors.append(item['path'])
    if errors:
        raise ValueError('Environment manifest mismatch: '+str(errors))
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('REVIVER_', 'LAZARUS_')) and k not in ('JAVA_HOME', 'ANDROID_HOME', 'ANDROID_SDK_ROOT')}
    env['PATH'] = os.path.join(env.get('SystemRoot', r'C:\Windows'), 'System32')
    with tempfile.TemporaryDirectory(prefix='portable-check-') as temporary:
        env['LAZARUS_STORAGE_ROOT'] = temporary
        env['LAZARUS_MIGRATE_LEGACY'] = '0'
        report = Path(temporary)/'self-test.json'
        result = subprocess.run([str(folder/'Lazarus-Agent.exe'), '--self-test', '--self-test-report', str(report)],
                                env=env, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or not report.is_file():
            raise RuntimeError('Portable self-test failed: '+str(result.returncode))
        payload = json.loads(report.read_text(encoding='utf-8'))
        for name in ('sdk', 'java'):
            path = Path(payload['environment'][name]).resolve()
            if folder/'env' not in path.parents:
                raise ValueError('Unexpected machine dependency: '+name)
        if not payload['environment']['webview2_bundled']:
            raise ValueError('Missing bundled WebView2')
        tools = {'java': [folder/'env/jre/bin/java.exe', '-version'],
                 'adb': [folder/'env/android-sdk/platform-tools/adb.exe', 'version'],
                 'apktool': [folder/'env/jre/bin/java.exe', '-jar', folder/'env/apktool/apktool_2.12.1.jar', '--version']}
        checks = {}
        for name, command in tools.items():
            tool = subprocess.run(list(map(str, command)), env=env, capture_output=True, timeout=60,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
            checks[name] = tool.returncode
            if tool.returncode:
                raise RuntimeError('Bundled tool failed: '+name)
    result = {'files_verified': len(manifest['files']), 'env_bytes': manifest['bytes'],
              'apis': manifest['apis'], 'self_test_exit': 0, 'tool_exits': checks,
              'developer_path_removed': True, 'environment': payload['environment']}
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--dist', type=Path, default=Path('dist'))
    cli.add_argument('--receipt', type=Path, default=Path('build/portable-env-verification.json'))
    args = cli.parse_args()
    verify(args.dist, args.receipt)
