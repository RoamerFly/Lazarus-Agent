# 文件交接候选的外部 UID 复测

工作台的固定五步覆盖应用内交接。独立探针先安装在不同 UID 下，默认保持空闲。随后显式发送候选使用的同一个 URI，分别记录应用内写入前和写入后的接收界面。也可单独发送伪造的字节数据。两种输入均由探针发起，安装探针本身不会触发交接。

    python tools/replay_record_caller.py --apk <留档候选.apk> --sha256 <候选 SHA-256> --uri-before-and-after-share --record-uri <从候选核对的完整 content URI> --out build/record-caller-review

旧命令中的 `--record-path` 仍可用于默认 authority 的两个历史路径。自定义 URI 只接受字面 authority 和路径。复测要求 Android 16 x86_64 专用模拟器，报告核对两个 UID、候选与设备安装哈希、实际发出的 URI 及前后 UI。结论范围只覆盖记录中的输入和候选。

本次已通过 Python 单元测试及探针 APK 编译、签名和包名回读。新版探针设备校准将在本版回归及演示释放模拟器后执行。旧版探针对三份 v0.6.22 候选的设备发现继续独立留档。[历史设备回执](0.6.22-external-uri-before-after.json)
