"""Inspect and change a single manifest Activity's orientation."""
from xml.dom import minidom
import re


ANDROID = "http://schemas.android.com/apk/res/android"
ORIENTATIONS = {
    "landscape": "landscape",
    "portrait": "portrait",
    "follow-system": "unspecified",
}


def _children(node, tag):
    return [child for child in node.childNodes if child.nodeName == tag]


def _android(node, key):
    return node.getAttributeNS(ANDROID, key)


def _manifest(decoded):
    path = decoded / "AndroidManifest.xml"
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("应用清单过大")
    data = path.read_bytes()
    if b"\0" in data or b"<!DOCTYPE" in data.upper():
        raise ValueError("应用清单格式不受支持")
    document = minidom.parseString(data)
    manifest = document.documentElement
    apps = _children(manifest, "application")
    package = manifest.getAttribute("package")
    if manifest.tagName != "manifest" or len(apps) != 1 or not package:
        raise ValueError("无法确定应用清单中的页面")
    return path, document, package, _children(apps[0], "activity")


def _component(package, node):
    name = _android(node, "name")
    if not re.fullmatch(r"[A-Za-z0-9_.$]{1,200}", name):
        return None
    if name.startswith("."):
        return package + name
    return name if "." in name else package + "." + name


def inspect(decoded):
    _, _, package, nodes = _manifest(decoded)
    activities = []
    for node in nodes[:80]:
        name = _component(package, node)
        if name:
            activities.append({
                "activity": name,
                "label_ref": _android(node, "label")[:100],
                "orientation": _android(node, "screenOrientation")[:60] or "unspecified",
                "intent_actions": [_android(action, "name")[:120]
                                   for filt in _children(node, "intent-filter")[:8]
                                   for action in _children(filt, "action")[:4]],
            })
    return {"activities": activities, "truncated": len(nodes) > 80}


def apply(decoded, activity, orientation):
    if orientation not in ORIENTATIONS:
        raise ValueError("页面方向超出支持范围")
    path, document, package, nodes = _manifest(decoded)
    matches = [node for node in nodes if _component(package, node) == activity]
    if len(matches) != 1:
        raise ValueError("没有找到唯一对应的页面")
    node, = matches
    previous = _android(node, "screenOrientation") or "unspecified"
    value = ORIENTATIONS[orientation]
    if previous == value:
        raise ValueError("该页面已声明所需方向，无需重打包")
    # Reuse the existing Android namespace prefix so other attributes retain
    # their original spelling and namespace bindings.
    attr = node.getAttributeNodeNS(ANDROID, "name")
    node.setAttributeNS(ANDROID, f"{attr.prefix}:screenOrientation", value)
    path.write_bytes(document.toxml(encoding="utf-8"))
    return {"kind": "activity-orientation", "activity": activity, "before": previous, "after": value}
