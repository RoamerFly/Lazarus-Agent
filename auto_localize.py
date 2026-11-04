"""Build a signed localization candidate from a selected immutable workbench input."""

from pathlib import Path
import re
import tempfile

from apk_edit_build import build_modified
from auto_translation import translations
import localization_edit
from reviver import INSTALL_FLOOR, repair


def transform(decoded, use_model, progress):
    assets = decoded / "assets"
    if (assets / "bin/Data").is_dir() or any(assets.rglob("*.unity3d")):
        from unity_localization import transform as unity_transform

        return unity_transform(decoded, use_model, progress)
    unsupported = {"libUE4.so", "libUnreal.so", "libgodot_android.so", "libcocos2dcpp.so", "libcocos2dx.so"}
    if any(path.name in unsupported for path in (decoded / "lib").rglob("*.so")):
        raise ValueError("识别到游戏引擎，但目前没有适用的内部语言表和字库适配器")
    rows = [
        row
        for row in localization_edit.catalog(decoded)
        if (not row["translated"] or row["translated"] == row["original"])
        and not re.search(r"(?i)api_?key|secret|token|password|client_?id|url|endpoint", row["name"])
    ]
    from indexed_text import discover

    pools = discover(assets)
    pool_text = {value for pool in pools.values() for _, value in pool.rows}
    if pools:
        progress(f"识别到 {len(pools)} 张 Java 索引文本表，包含 {len(pool_text)} 个不同文本")
    if not rows and not pools:
        raise ValueError("未发现可补译的 Android 字符串或支持的游戏文本表")
    progress(f"发现 {len(rows)} 个候选 Android 字符串，检查已有中文资源和格式占位符")
    sources = [row["original"] for row in rows]
    mapped, model_count = translations([*sorted(pool_text), *sources], use_model, progress)
    applied = 0
    for row in rows:
        src = row["original"]
        if src not in mapped:
            continue
        zh = mapped[src]
        criteria = {
            "translations": {src: zh},
            "resources": {src: row["name"]},
            "checks": [{"original": src, "translated": zh}],
        }
        plan = localization_edit.inspect(decoded, criteria)["plan"]
        localization_edit.apply(decoded, plan)
        applied += 1
    if pools:
        from android_game_fonts import update

        updated_tables = updated_entries = 0
        for path, pool in pools.items():
            matches = sum(value in mapped for _, value in pool.rows)
            if matches:
                path.write_bytes(pool.translated(mapped))
                updated_tables += 1
                updated_entries += matches
        if not updated_tables:
            raise ValueError("识别到游戏文本表，但未得到可写入的游戏译文。可启用模型翻译后重试")
        font_report = update(assets, mapped.values())
        progress(
            f"已回读 {updated_tables} 张改写的文本表，核对 {len(font_report['outline_fonts'])} 个轮廓字体，"
            f"向 {font_report['bitmap_atlases_updated']} 张位图字库补入 {font_report['bitmap_glyphs_added']} 个字形"
        )
        return {
            "kind": "android-indexed-text-zh",
            "translated": applied + updated_entries,
            "discovered": len(rows) + sum(len(t.rows) for t in pools.values()),
            "resources_updated": applied,
            "tables_discovered": len(pools),
            "tables_updated": updated_tables,
            "game_entries_updated": updated_entries,
            "model_translations": model_count,
            **font_report,
        }
    return {
        "kind": "android-resources-zh",
        "discovered": len(rows),
        "translated": applied,
        "model_translations": model_count,
    }


def build(source, destination, api, tools, use_model, progress):
    from diagnose import inspect_package
    from asset_native_repair import replacement_map

    progress("检查输入 APK、原生库和所选 Android 的安装门槛")
    facts = inspect_package(source, tools)
    input_sha256 = facts["sha256"]
    base = source
    floor = INSTALL_FLOOR[api]
    rules = []
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        if replacement_map(base):
            base = Path(tmp) / "native.apk"
            fix = repair(
                source,
                base,
                api,
                tools,
                "embedded-native-payload",
                modernize_crt=facts["target_sdk"] < floor,
            )
            rules.extend(fix["rules"])
            facts = inspect_package(base, tools)
        if facts["target_sdk"] < floor:
            compatible = Path(tmp) / "compatible.apk"
            fix = repair(base, compatible, api, tools, "installation-floor")
            rules.extend(fix["rules"])
            base = compatible
        progress("解包并分析可翻译文本及显示字体")
        result = build_modified(
            base, destination, api, tools, lambda decoded: transform(decoded, use_model, progress)
        )
    result["input_sha256"] = input_sha256
    result["compatibility_rules"] = rules
    return result
