"""Recognize the few Android dialogs allowed to interrupt isolated APK tests."""

COMPATIBILITY_NOTICES = (
    "This app was built for an older version of Android",
    "This app isn't compatible with the latest version of Android.",
)


def system_dialog(tree):
    fields = ("text", "resource-id", "bounds")
    controls = {}
    for node in tree.iter("node"):
        resource = node.get("resource-id")
        if node.get("package") == "android" and resource in {
            "android:id/alertTitle", "android:id/aerr_wait", "android:id/aerr_close",
            "android:id/message", "android:id/button1",
        }:
            controls.setdefault(resource, []).append({key: node.get(key, "")[:240] for key in fields})

    def one(resource):
        matches = controls.get("android:id/" + resource, ())
        return matches[0] if len(matches) == 1 else {}

    title, wait = one("alertTitle"), one("aerr_wait")
    message, ok = one("message"), one("button1")
    if "android:id/aerr_wait" in controls or "android:id/aerr_close" in controls:
        if title.get("text") == "System UI isn't responding" and wait.get("text") == "Wait":
            return "system-ui-anr", wait, title.get("text")
        return "other-anr", None, title.get("text")
    if message.get("text", "").startswith(COMPATIBILITY_NOTICES) and ok.get("text") == "OK":
        return "legacy-target-notice", ok, None
    return None, None, None
