"""Run a user-specified UI operation on an isolated emulator and preserve evidence."""
import re
import time
import xml.etree.ElementTree as ET

from bench_device import ADB, invoke, output, shell


def probe(serial,pkg,task,dest):
    dest.mkdir()
    def snapshot(label):
        shell(serial,"uiautomator","dump","/data/local/tmp/reviver-task.xml")
        xml=invoke([ADB,"-s",serial,"exec-out","cat","/data/local/tmp/reviver-task.xml"]).stdout
        (dest/(label+".xml")).write_bytes(xml)
        shot=invoke([ADB,"-s",serial,"exec-out","screencap","-p"]).stdout
        if shot.startswith(b"\x89PNG"):
            (dest/(label+".png")).write_bytes(shot)
        return ET.fromstring(xml)
    def is_match(node,value):
        return value in (node.get("text"),node.get("content-desc"),node.get("resource-id"))
    def tap(node):
        bounds=list(map(int,re.findall(r"\d+",node.get("bounds",""))))
        if len(bounds)!=4:raise ValueError("控件位置无效")
        shell(serial,"input","tap",str((bounds[0]+bounds[2])//2),str((bounds[1]+bounds[3])//2))
    tree=snapshot("before")
    system_nodes={n.get("resource-id"):n for n in tree.iter("node") if n.get("package")=="android"}
    message=system_nodes.get("android:id/message")
    ok=system_nodes.get("android:id/button1")
    compatibility_notices=("This app was built for an older version of Android",
                           "This app isn't compatible with the latest version of Android.")
    if (message is not None and message.get("text","").startswith(compatibility_notices)
            and ok is not None and ok.get("text")=="OK"):
        tap(ok)
        time.sleep(.5)
        tree=snapshot("after-legacy-notice")
    node=next((n for n in tree.iter("node") if n.get("package")==pkg and is_match(n,task["tap"])),None)
    if node is None:
        return {"result":"insufficient","message":"找不到指定控件。请核对文案、系统弹窗和应用当前页面。"}
    activities=shell(serial,"dumpsys","activity","top","-c")
    owner=re.findall(r"(?m)^\s*ACTIVITY "+re.escape(pkg)+r"/\S+ [^\r\n]*?\bpid=(\d+)",activities)
    # Only clear the crash buffer on our private lab AVD before this individual action.
    invoke([ADB,"-s",serial,"logcat","-b","crash","-c"])
    tap(node)
    time.sleep(1)
    after=snapshot("after")
    pid_filter=["--pid",owner[-1]] if owner else []
    crash=output(invoke([ADB,"-s",serial,"logcat","-b","crash","-d",*pid_filter]))
    process=re.search(r"AndroidRuntime:\s+Process: ([\w.:]+), PID: (\d+)",crash)
    if process and (process.group(2) in owner or process.group(1).split(":")[0]==pkg) and "FATAL EXCEPTION" in crash:
        (dest/"crash.txt").write_text(crash,encoding="utf-8")
        match=re.search(r"NoClassDefFoundError: Failed resolution of: L(org/apache/http/[A-Za-z0-9_/$]+);",crash)
        exception=re.search(r"AndroidRuntime:\s+(?:Caused by: )?(android\.os\.FileUriExposedException)\b",crash)
        failure={"result":"failed","message":"核心操作触发应用崩溃","missing_class":match.group(1) if match else None}
        if exception:
            failure.update(exception=exception.group(1),message="共享文件时触发 FileUriExposedException")
        return failure
    for n in after.iter("node"):
        if n.get("package")==pkg and is_match(n,task["expect"]):
            return {"result":"passed","message":"操作后在目标应用内找到预期内容："+task["expect"]}
    return {"result":"insufficient","message":"操作后未找到预期内容，详见界面树。"}
