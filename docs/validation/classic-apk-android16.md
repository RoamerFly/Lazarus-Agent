# 六款历史 APK 的 Android 16 原包实测

2026-11-24。本次使用专用 AOSP Android 16/API 36 x86_64 模拟器，验证未经修改的下载 APK，使用默认安装命令，无低版本安装豁免、无 root 绕过、无模型调用。每份原包测试前核对下载清单 SHA-256。

| 原包 | 系统看到的 target SDK | 默认全新安装 | 启动与功能范围 |
|---|---:|---|---|
| Sensor Readout 2.4 | 17 | 拒绝 | 未能安装，未测试运行 |
| ODK Collect 1.2.2 | 7（安装器报告的有效值） | 拒绝 | 未能安装，未测试运行 |
| GPSLogger 27-fdroid | 5 | 拒绝 | 未能安装，未测试运行 |
| WiFiAnalyzer 1.4.2.33 | 22 | 拒绝 | 未能安装，未测试运行 |
| Open Camera 1.32 | 22 | 拒绝 | 未能安装，未测试运行 |
| phyphox 1.1.0 | 28 | 成功 | 启动命令成功、前台为目标应用，观察到 18 个应用节点和首次 Warning 页；无 crash buffer 记录；完整实验功能未测试 |

前五款的真实错误均为 `INSTALL_FAILED_DEPRECATED_SDK_VERSION`，系统消息要求 target SDK 至少为 24。因此可以将其描述为“Android 16 默认策略下不能直接全新安装”，不能将这些结果写成“安装成功后必然闪退”，也不能推断已安装旧版本升级系统后的表现。

phyphox 设备安装回读 SHA-256 为 `d1b3f3d67627be6a392a020a4868d555c383f602e9dc5588c804c5a581f5ab76`，与原始 APK 一致。它不应列入“确认无法运行”的演示正例，保留为可安装启动的对照。

本次未生成或修改候选，不代表任何软件已修复；结果范围是本次专用模拟器，不扩展为所有厂商真机结论。测试完成后关闭专用模拟器。

逐款安装错误、环境指纹、原包哈希和 phyphox 启动结果见 [原始 JSON 报告](classic-apk-android16.json)。原始截图和界面树保存在本机 `build/classic-baseline/20261124T062949Z/de.rwth_aachen.phyphox/`。可执行 `python tools/check_classic_apks.py` 重新生成一份独立基线报告。
