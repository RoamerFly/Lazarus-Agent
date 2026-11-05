# 行为预检的 Android 16 设备验证

使用开发提交 `5fdbc0c` 和 Android 16 API 36 x86_64 专用模拟器。将自有归档阅读器原包作为故障输入，另取 P1B 已保存的归档阅读器规则候选。通过工作台的 `Session.dispatch` 调用预检工具，固定执行 P02 的六步验收。

原包安装启动通过，前两步通过。第 3 步预期显示 `Read OK: HTTP/1.1`，实际因缺少 `org.apache.http.ProtocolVersion` 崩溃。工具返回了应用调用位置和目标系统列有 `org.apache.http.legacy` 的证据。

已知候选安装回读 SHA-256 与输入文件一致，预检通过六步。随后另行重新安装原包与候选，通过正式独立验收。交付标记来自后一次运行。该验证由脚本选择样本和调用工具，验证了失败证据能进入修复循环；实际模型如何选用工具单独计量。[设备摘要](agent-rehearsal-device.json)
