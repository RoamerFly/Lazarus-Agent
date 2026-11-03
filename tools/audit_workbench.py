"""Verify web downloads against inputs, signed-file evidence and the actual functional results."""
import hashlib
import json
from pathlib import Path
from audit_note_trials import audit

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/"evidence/peer-web"
def read(id):
    return json.loads((P/(id+"-report.json")).read_text(encoding="utf-8"))
def sha(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def verify():
    results=audit(ROOT)
    for id,source,rule in [
        ("oi-api35",ROOT/"old_apk/OI-Notepad-1.3.apk","installation-floor"),
        ("archive-broken-api35",ROOT/"fixtures/archive-reader/archive-reader-broken.apk","platform-http-library"),
        ("archive-control-api34",ROOT/"fixtures/archive-reader/archive-reader-control.apk",None),
        ("archive-control-api35",ROOT/"fixtures/archive-reader/archive-reader-control.apk",None)]:
        rep=read(id)
        assert rep["package"]["sha256"]==sha(source)
        digest=sha(P/(id+"-download.apk"))
        assert rep["export"]["sha256"]==digest
        assert rep["diagnosis"]["advice"]["source"]=="rules"
        if rule:
            assert rep["patch"]["rule"]==rule and digest!=sha(source)
            assert rep["acceptance"]["result"]=="passed"
            if rule=="platform-http-library":
                assert rep["tests"][0]["functional_test"]["missing_class"]=="org/apache/http/ProtocolVersion"
                assert rep["acceptance"]["message"].endswith("Read OK: HTTP/1.1")
            else:
                assert rep["acceptance"]["installed_sha256"]==digest
        else:
            assert sha(source)==digest and rep["export"]["file"]=="original.apk"
        results.append(id+" verified")
    wrong=read("archive-control-api34-wrong-expectation")
    assert wrong["export"] is None and wrong["diagnosis"]["advice"]["action"]=="report-limit"
    limit=read("google-my-tracks-api35-limit")
    assert limit["export"] is None and "INSTALL_FAILED_NO_MATCHING_ABIS" in limit["tests"][0]["install"]["output"]
    for filename,api in [("api34.json",34),("repair.json",35),("api36.json",36)]:
        rep=json.loads((ROOT/"evidence/ttpod"/filename).read_text(encoding="utf-8"))
        assert rep["tests"][0]["api"]==api and rep["export"] is None
        assert rep["package"]["sha256"]=="377f12f4a1c1cbd112178ef24dbaa1e5bc4cbdd16731916d4d689ab15fd366fb"
        assert "INSTALL_FAILED_NO_MATCHING_ABIS" in rep["tests"][0]["install"]["output"]
    for name,expected in [("permission","com.google.android.permissioncontroller"),
                          ("settings","com.android.settings"),("app","org.ggbond.archivereader")]:
        rep=json.loads((ROOT/"evidence/foreground"/(name+".json")).read_text(encoding="utf-8"))
        launch=rep["tests"][-1]["launch"]
        assert launch["exit"]==0 and "Status: ok" in launch["output"] and launch["focused_package"]==expected
        if name=="app":
            assert rep["export"]["level"]=="启动已验证，功能待核验" and rep["export"]["sha256"]==rep["package"]["sha256"]
        else:
            assert rep["export"] is None
    results.extend(["TTPod API 34/35/36 ABI limits verified","app, permission and settings foreground records verified"])
    fixture_hash=sha(ROOT/"fixtures/archive-reader/archive-reader-file-share.apk")
    for api,floor in [(34,23),(35,24),(36,24)]:
        matches=list((ROOT/"evidence/release-replays").glob(f"file-share-{api}-*/report.json"))
        verified=False
        for report in matches:
            rep=json.loads(report.read_text(encoding="utf-8"))
            if rep["package"]["sha256"]!=fixture_hash:
                continue
            baseline,candidate=rep["tests"]
            assert "INSTALL_FAILED_DEPRECATED_SDK_VERSION" in baseline["install"]["output"]
            assert rep["api"]==candidate["api"]==api and candidate["install"]["exit"]==0
            assert rep["task"]=={"tap":"Share local record","expect":"Shared"}
            assert rep["patch"]["before"]==22 and rep["patch"]["after"]==floor
            func=candidate["functional_test"]
            probe=report.parent/func["evidence_folder"]
            if api==34:
                assert rep["export"]["sha256"]==rep["patch"]["sha256"] and func["result"]=="passed"
                assert 'text="Shared"' in (probe/"after.xml").read_text(encoding="utf-8")
            else:
                assert rep["export"] is None and func["exception"]=="android.os.FileUriExposedException"
                assert rep["candidate_diagnosis"]["advice"]["action"]=="report-limit"
                assert func["exception"] in (probe/"crash.txt").read_text(encoding="utf-8")
            verified=True
        assert verified, f"No file-sharing evidence for API {api} and shipped fixture"
    results.append("same file-sharing input verified on Android 14, 15 and 16")
    return results+["wrong expectation: export blocked","unavailable ABI: export blocked"]


if __name__=="__main__":
    print("\n".join(verify()))
