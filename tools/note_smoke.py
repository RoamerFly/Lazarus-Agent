"""Interactive UI acceptance for the authorized OI Notepad lab fixture."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET


PACKAGE = "org.openintents.notepad"
MAIN = PACKAGE + "/.noteslist.NotesList"


def exercise(adb, serial, output):
    if output.exists():
        raise FileExistsError("Use a new evidence folder")
    def cmd(*parts):
        return subprocess.check_output([adb,"-s",serial,*parts], timeout=35,
                                       creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if not serial.startswith("emulator-") or not cmd("emu","avd","name").startswith(b"Reviver"):
        raise ValueError("Requires a dedicated Reviver lab AVD")
    output.mkdir(parents=True)
    note = "Reviver-" + uuid.uuid4().hex[:12]
    trace = {"note":note,"utc":datetime.now(timezone.utc).isoformat(),"serial":serial,
             "api":cmd("shell","getprop","ro.build.version.sdk").decode().strip(),"steps":[]}
    apk_path=cmd("shell","pm","path",PACKAGE).decode().strip().removeprefix("package:")
    trace["installed_sha256"]=hashlib.sha256(cmd("exec-out","cat",apk_path)).hexdigest()
    def tree():
        cmd("shell","uiautomator","dump","/data/local/tmp/reviver-ui.xml")
        return cmd("exec-out","cat","/data/local/tmp/reviver-ui.xml")
    def locate(xml, key, value):
        return next((n for n in ET.fromstring(xml).iter("node") if n.get(key)==value),None)
    def tap(n):
        x1,y1,x2,y2=map(int,re.findall(r"\d+",n.get("bounds")))
        cmd("shell","input","tap",str((x1+x2)//2),str((y1+y2)//2))
        time.sleep(.6)
    def start():
        cmd("shell","am","start","-W","-n",MAIN)
    def save(name,xml):
        (output/(name+".xml")).write_bytes(xml)
        (output/(name+".png")).write_bytes(cmd("exec-out","screencap","-p"))
        trace["steps"].append(name)
    try:
        cmd("shell","wm","dismiss-keyguard")
        cmd("shell","am","force-stop",PACKAGE)
        start()
        for _ in range(9):
            xml=tree()
            add=locate(xml,"content-desc","Add note")
            if add is not None:
                break
            for title in ("OK","Accept","Continue"):
                action=next((n for n in ET.fromstring(xml).iter("node")
                             if n.get("class")=="android.widget.Button" and
                             n.get("text","").casefold()==title.casefold()),None)
                if action is not None:
                    tap(action)
                    break
            else:
                start()
        else:
            raise RuntimeError("Note list did not appear")
        tap(add)
        edit=locate(tree(),"resource-id",PACKAGE+":id/note")
        if edit is None:
            raise RuntimeError("Editor is missing")
        cmd("shell","input","text",note)
        xml=tree()
        if locate(xml,"text",note) is None:
            raise RuntimeError("Text entry failed")
        save("01-entered",xml)
        for _ in range(3):
            cmd("shell","input","keyevent","4")
            time.sleep(.4)
            xml=tree()
            if locate(xml,"content-desc","Add note") is not None and locate(xml,"text",note) is not None:
                break
        else:
            raise RuntimeError("Note missing from list after save")
        save("02-saved",xml)
        cmd("shell","am","force-stop",PACKAGE)
        start()
        xml=tree()
        item=locate(xml,"text",note)
        if item is None or locate(xml,"content-desc","Add note") is None:
            raise RuntimeError("Note missing after cold start")
        tap(item)
        xml=tree()
        editor=locate(xml,"resource-id",PACKAGE+":id/note")
        if editor is None or editor.get("text")!=note:
            raise RuntimeError("Reopened note body mismatch")
        save("03-reopened",xml)
        trace["result"]="passed"
    except Exception as error:
        trace.update(result="failed",error=str(error))
        raise
    finally:
        (output/"result.json").write_text(json.dumps(trace,indent=2),encoding="utf-8")
    print(json.dumps(trace,indent=2))


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--adb",default="adb")
    p.add_argument("--serial",required=True)
    p.add_argument("--output",required=True,type=Path)
    exercise(**vars(p.parse_args()))
