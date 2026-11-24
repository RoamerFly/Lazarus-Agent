"""Assemble relocatable, allow-listed dependencies beside the Windows EXE."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from offline_kit import discover_sdk, discover_java_home, _sha256
from apk_edit_build import APKTOOL_VERSION, _validate_apktool, apktool_jar


def assemble(output, sdk, java, webview, apktool):
    output = output.resolve()
    sdk, java = sdk.resolve(strict=True), java.resolve(strict=True)
    if any(output == p or p in output.parents for p in (sdk, java)):
        raise ValueError("Environment output must be outside source components")
    for p in (sdk/'platform-tools/adb.exe', sdk/'emulator/emulator.exe',
              java/'bin/java.exe', java/'bin/keytool.exe'):
        if not p.is_file():
            raise FileNotFoundError(p)
    if not _validate_apktool(apktool):
        raise ValueError("Apktool pinned digest mismatch")
    if not (webview/'msedgewebview2.exe').is_file():
        raise FileNotFoundError("A Microsoft Fixed Version WebView2 runtime is required")
    output.mkdir(parents=True, exist_ok=True)

    def copy(source, target):
        if source.resolve() != target.resolve():
            # copytree dereferences junctions: portable env must not point to this PC.
            shutil.copytree(source, target, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.lock'))

    for name in ('platform-tools', 'emulator', 'build-tools', 'system-images', 'licenses'):
        if (sdk/name).is_dir():
            copy(sdk/name, output/'android-sdk'/name)
    copy(java, output/'jre')
    copy(webview, output/'webview2')
    (output/'apktool').mkdir(exist_ok=True)
    apktool_target = output/'apktool'/f'apktool_{APKTOOL_VERSION}.jar'
    if apktool.resolve() != apktool_target.resolve():
        shutil.copy2(apktool, apktool_target)
    files = [{'path': p.relative_to(output).as_posix(), 'bytes': p.stat().st_size,
              'sha256': _sha256(p)} for p in sorted(output.rglob('*'))
             if p.is_file() and p.name != 'manifest.json']
    manifest = {'schema': 1, 'platform': 'Windows x64', 'apis': [
        int(p.name.removeprefix('android-')) for p in (output/'android-sdk/system-images').glob('android-*')],
        'files': files, 'bytes': sum(f['bytes'] for f in files)}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (output.parent/'portable.flag').write_text('portable\n', encoding='ascii')
    (output.parent/'old_apk').mkdir(exist_ok=True)
    (output.parent/'环境自检.cmd').write_bytes(
        b'@echo off\r\ncd /d "%~dp0"\r\nstart /wait "" Lazarus-Agent.exe --self-test --self-test-report portable-check.json\r\n'
        b'echo Exit code: %errorlevel%\r\npause\r\n')
    shutil.copy2(ROOT/'便携版使用说明.md', output.parent/'使用说明.md')
    return {'files': len(files), 'bytes': manifest['bytes'], 'apis': manifest['apis']}


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--out', type=Path, default=ROOT/'dist/env')
    cli.add_argument('--sdk', type=Path)
    cli.add_argument('--java', type=Path)
    cli.add_argument('--webview', type=Path)
    cli.add_argument('--apktool', type=Path)
    args = cli.parse_args()
    sdk, java = args.sdk or discover_sdk(), args.java or discover_java_home()
    if sdk is None or java is None:
        cli.error('Prepare an Android runtime first, or supply --sdk and --java')
    print(json.dumps(assemble(args.out, sdk, java, args.webview or args.out/'webview2',
                              args.apktool or apktool_jar()), indent=2))
