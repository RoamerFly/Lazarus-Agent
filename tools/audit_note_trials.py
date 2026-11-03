"""Cross-check APK hashes and OI Notepad trials on independent AVDs."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(root):
    original=sha(root/"old_apk/OI-Notepad-1.3.apk")
    devices=set()
    notes=set()
    results=[]
    for label, smoke in [("api35-B","notes-01"),("api35-C","notes-01"),
                         ("api34-A","notes-02"),("api34-B","notes-01"),("api36-independent","notes-01")]:
        folder=root/"evidence"/label
        trial=read(folder/"install-trial.json")
        ui=read(folder/smoke/"result.json")
        api=int(trial["api"])
        assert str(api)==label[3:5] and str(api)==ui["api"]
        previous=trial["original"]
        candidate=trial["candidate"]
        digest=sha(root/"artifacts/candidates"/f"OI-Notepad-1.3-api{api}.apk")
        assert previous["sha256"]==original and previous["install"]["exit"]!=0
        assert "INSTALL_FAILED_DEPRECATED_SDK_VERSION" in previous["install"]["output"]
        assert candidate["sha256"]==ui["installed_sha256"]==digest
        assert candidate["install"]["exit"]==0 and ui["result"]=="passed"
        for entry in (previous,candidate):
            assert entry["install"]["command"][:1]==["install"] and len(entry["install"]["command"])==2
        assert trial["serial"]==ui["serial"] and trial["avd"] not in devices and ui["note"] not in notes
        assert ui["steps"]==["01-entered","02-saved","03-reopened"]
        for step in ui["steps"]:
            ui_tree=ET.parse(folder/smoke/(step+".xml"))
            assert any(node.get("text")==ui["note"] for node in ui_tree.iter("node"))
            assert (folder/smoke/(step+".png")).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        devices.add(trial["avd"])
        notes.add(ui["note"])
        results.append(f"{label} {trial['avd']} {ui['note']} PASS")
    return results


if __name__=="__main__":
    print("\n".join(audit(Path(__file__).resolve().parents[1])))
