"""Record unchanged historical APK baselines on a private API 36 emulator."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('REVIVER_SDK', str(ROOT/'dist/env/android-sdk'))
os.environ.setdefault('REVIVER_JAVA_HOME', str(ROOT/'dist/env/jre'))
os.environ.setdefault('REVIVER_LAB', r'E:\Agent\lazarus-classic-baseline')
from bench_device import Device, TOOLS, invoke, ADB, shell
from agent_device import AgentDevice
from android_dialog import system_dialog


def main():
    catalog = json.loads((ROOT/'docs/classic-apk-catalog.json').read_text(encoding='utf-8'))
    output = ROOT/'build/classic-baseline'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output.mkdir(parents=True, exist_ok=False)
    report = {'tested_at': datetime.now(timezone.utc).isoformat(), 'scope': 'unchanged APK, default install policy, private Android 16 x86_64',
              'installation_bypass_used': False, 'cases': []}
    device = Device(allow_existing=False)
    try:
        serial = device.ensure('classic-original-baseline', 36, lambda text: print(text, flush=True))
        report['environment'] = {key: shell(serial, 'getprop', key) for key in (
            'ro.build.version.sdk', 'ro.build.version.release', 'ro.product.cpu.abilist', 'ro.build.fingerprint')}
        report['environment']['avd'] = shell(serial, 'getprop', 'ro.boot.qemu.avd_name')
        for sample in catalog['samples']:
            apk = ROOT/'old_apk'/sample['file']
            if not apk.is_file() and sample.get('control_archive'):
                apk = ROOT/sample['control_archive']
            assert hashlib.sha256(apk.read_bytes()).hexdigest() == sample['sha256']
            folder = output/sample['package']
            folder.mkdir()
            shell(serial, 'logcat', '-b', 'crash', '-c')
            tester = AgentDevice(device, 'classic-original-baseline', 36, TOOLS, print)
            item = {'file': sample['file'], 'package': sample['package'], 'sha256': sample['sha256']}
            try:
                result = tester.test(apk, sample['sha256'], capture_ui=False)
                item['test'] = result
                if result['install']['exit'] != 0:
                    item['status'] = 'default-install-rejected'
                    item['reason'] = re.findall(r'INSTALL_FAILED_[A-Z_]+', result['install']['output'])
                else:
                    # Dismiss only explicitly recognized compatibility notices.
                    for attempt in range(3):
                        time.sleep(2)
                        tree = tester.tree()
                        kind, button, _ = system_dialog(tree)
                        if kind == 'legacy-target-notice' and button:
                            x1,y1,x2,y2 = map(int,re.findall(r'\d+',button['bounds']))
                            shell(serial,'input','tap',str((x1+x2)//2),str((y1+y2)//2))
                        else:
                            break
                    (folder/'launch.xml').write_text(ET.tostring(tree,encoding='unicode'),encoding='utf-8')
                    (folder/'launch.png').write_bytes(invoke([ADB,'-s',serial,'exec-out','screencap','-p']).stdout)
                    item['ui_text'] = [n.get('text') for n in tree.iter('node') if n.get('package') == sample['package'] and n.get('text')]
                    item['app_nodes'] = sum(n.get('package') == sample['package'] for n in tree.iter('node'))
                    item['window'] = tester.window_context()
                    item['status'] = 'app-screen-observed' if item['app_nodes'] else 'installed-no-app-screen-observed'
                    item['core_function_status'] = 'not-tested'
                    shell(serial,'am','force-stop',sample['package'])
                item['crash_log'] = shell(serial,'logcat','-b','crash','-d','-v','brief')
            except Exception as error:
                item.update(status='test-error',error=str(error))
            report['cases'].append(item)
            (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(item,ensure_ascii=False),flush=True)
    finally:
        device.stop()
    print(str(output/'report.json'),flush=True)


if __name__ == '__main__':
    main()
