# Agent 原包复现与设备诊断

对开发分支 `4aa81a5` 使用 Android 16 API 36 x86_64 专用模拟器和团队自有归档阅读器。调用 `test_original` 安装并启动原包，核对安装回读哈希。随后通过 `device_action` 点击“Open archived record”。全程构建次数为零，记录的验收对象为原包。

工具返回本次操作的 `NoClassDefFoundError`、应用调用位置和缺失类 `org.apache.http.ProtocolVersion`。目标设备的 PackageManager 列出了 `org.apache.http.legacy`，工具将其作为兼容库线索返回。[设备结果](agent-runtime-evidence-dev.json)

另用 P1B 巡检单已发生的模拟器异常检查解析器，确认结构化摘要保留错误的方法签名。单元测试覆盖包和进程匹配、旧异常排除、界面树失败时取证、安装失败后的动作限制及原包和候选证据隔离。后续按原构建额度复跑已见任务，测量模型能否据此更稳定地交付候选。
