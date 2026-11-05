"""Check the v0.6.6 OI Notepad run against independent local evidence.

This replays assertions over saved workbench records. It does not start a
device, call the model, or estimate success rates for future runs.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "org.openintents.notepad"
TITLE = "android:id/action_bar_title"
SUMMARY = "android:id/summary"
EDITOR = PACKAGE + ":id/note"
TEMPLATE = "事项：\n时间：\n备注："


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def contains(step, expected):
    observed = {(node["resource-id"], node["text"]) for node in step["ui"]["nodes"]}
    return all(item in observed for item in expected)


def ordered_checks(steps, checks):
    remaining = iter(enumerate(steps, 1))
    results = []
    for name, action, argument, expected in checks:
        index = next((index for index, step in remaining if step["action"] == action and
                      step["text"] == argument and contains(step, expected)), None)
        require(index is not None, f"缺少按顺序观察到的结果：{name}")
        results.append({"check": name, "device_step": index})
    return results


def audit_run(state, artifact, input_hash, checks):
    require(state["package"]["package"] == PACKAGE, "样本包名不符")
    candidate = state["feature_candidates"][state["latest_feature_candidate"]]
    require(candidate["source_sha256"] == input_hash, "APK 输入哈希链不符")
    require(candidate["outcome"] == "candidate" and candidate["model"] == "deepseek-flash",
            "候选结果或模型记录不符")
    device = candidate["verification"]
    require(candidate["api"] == device["api"] == 36, "候选或设备 API 不符")
    require(device["install"]["exit"] == device["launch"]["exit"] == 0, "设备安装或启动未通过")
    output_hash = sha256(artifact)
    require(output_hash == candidate["sha256"] == device["installed_sha256"],
            "下载件、候选和设备安装包哈希不一致")
    trace = Counter((row["tool"], row["ok"]) for row in candidate["tool_trace"])
    require(all(trace[(name, True)] for name in ("replace_text", "build_apk", "test_apk", "finish")),
            "缺少实际修改、构建、测试或收尾的工具记录")
    return {
        "input_sha256": input_hash,
        "candidate_sha256": output_hash,
        "api": device["api"],
        "model": candidate["model"],
        "tool_calls": sum(trace.values()),
        "tool_errors": sum(count for (_, ok), count in trace.items() if not ok),
        "checks": ordered_checks(device["steps"], checks),
    }


def audit(records, artifacts, original):
    original_hash = sha256(original)
    first, followup = (json.loads((records / f"{name}.json").read_bytes()) for name in ("first", "followup"))
    require(first["package"]["sha256"] == followup["package"]["sha256"] == original_hash, "原包哈希不符")
    require(first["id"] == followup["id"], "追加需求未使用同一任务")
    count = audit_run(first, artifacts / "OI-Notepad-1.3-默认字符计数-Android16.apk", original_hash, [
        ("输入五个字符后标题显示计数", "input_text", "hello", [(TITLE, "[5] Create note"), (EDITOR, "hello")]),
        ("设置默认开启", "tap_text", "Settings", [(SUMMARY, "Show character count in editor")]),
        ("设置允许关闭", "tap_text", "Character count", [(SUMMARY, "Do not show character count in editor")]),
        ("关闭后重开笔记保留正文且不显示计数", "tap_text", "hello", [(TITLE, "Edit note"), (EDITOR, "hello")]),
    ])
    template = audit_run(followup, artifacts / "OI-Notepad-1.3-计数与新建模板-Android16.apk",
                         count["candidate_sha256"], [
        ("新建预填三行并继承计数", "tap_text", "Add note", [(TITLE, "[11] Create note"), (EDITOR, TEMPLATE)]),
        ("输入后重开保留正文且模板不重复", "tap_text", "TEST123事项：",
         [(TITLE, "[18] Edit note"), (EDITOR, "TEST123" + TEMPLATE)]),
    ])
    return {"mode": "historical-evidence-audit", "sample": PACKAGE,
            "runs": {"default_count": count, "append_template": template}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, default=ROOT / "build/generic-agent-066")
    parser.add_argument("--artifacts", type=Path, default=ROOT / "artifacts/verified")
    parser.add_argument("--original", type=Path, default=ROOT / "old_apk/OI-Notepad-1.3.apk")
    args = parser.parse_args()
    try:
        result = audit(args.records, args.artifacts, args.original)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        parser.exit(1, f"证据核验失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
