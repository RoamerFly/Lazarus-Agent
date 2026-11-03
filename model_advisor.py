"""Evidence-constrained diagnosis through a configurable chat completions endpoint."""
from model_settings import get_config
from model_tools import ModelToolError, run_agent


def rule_diagnosis(evidence, action, status):
    install = evidence["E5_install"]
    if action == "try-installation-floor-repair":
        ids = ["E1_target_sdk","E2_device_api","E5_install"]
        reason = (f"目标设备 API {evidence['E2_device_api']} 返回 {install}。"
                  f"原包目标 API 为 {evidence['E1_target_sdk']}，可尝试调整安装门槛，再做全新安装复测。")
    elif action in {"try-platform-http-library", "try-platform-shared-library"}:
        ids = ["E7_missing_class", "E8_platform_library"]
        reason = (f"指定操作失败时缺少 {evidence['E7_missing_class']}。设备已列出 "
                  f"{evidence['E8_platform_library']}，可声明该库后重做同一操作。")
    elif action == "try-embedded-native-payload":
        ids=["E10_embedded_native_payload","E11_embedded_payload_needed"]
        reason="安装包自身含有代理库引用的同架构实现。用这些实现生成签名候选，并在所选目标上重新安装、运行。"
    elif action == "try-abi-pruning":
        ids=["E7_missing_native_library","E9_abi_pruning_plan"]
        reason="按缺库证据和设备实际支持的架构，验证选择完整库集后的候选。"
    elif action == "test-original":
        ids = ["E5_install"]
        reason = "原包已通过系统安装检查，保留原签名，按启动与功能验收结果决定是否导出。"
    elif install == "INSTALL_FAILED_NO_MATCHING_ABIS":
        ids = ["E3_native_abis","E4_device_abis","E5_install"]
        reason = (f"系统返回 {install}。原生库架构为 {', '.join(evidence['E3_native_abis'])}，"
                  f"设备报告 {', '.join(evidence['E4_device_abis'])}。当前规则无法补齐缺失的机器码。")
    elif evidence.get("E9_exception")=="android.os.FileUriExposedException":
        ids=["E5_install","E6_functional_test","E9_exception"]
        reason="安装通过，但共享文件时系统报告 FileUriExposedException。应用向其他应用传递了 file:// 地址，现有规则无法修复这个分享操作。"
    elif "E6_functional_test" in evidence:
        ids = ["E5_install","E6_functional_test"]
        reason = f"安装结果为 {install}，指定操作结果为 {evidence['E6_functional_test']}。没有适用的修复依据。"
    else:
        ids = ["E5_install"]
        reason = f"安装结果为 {install}，没有适用的修复依据。"
    return {"source":"rules","model_status":status,"action":action,"reason":reason,"evidence_ids":ids}


def advise(evidence, allowed, fallback, inspect_apk=None, lookup_library=None):
    config = get_config()
    if not config["enabled"]:
        return rule_diagnosis(evidence, fallback, "not-configured")
    try:
        return run_agent(evidence, allowed, fallback, config, inspect_apk, lookup_library)
    except ModelToolError as exc:
        result = rule_diagnosis(evidence, fallback, "fallback:ModelToolError")
        result["model_error"] = str(exc)
        return result
