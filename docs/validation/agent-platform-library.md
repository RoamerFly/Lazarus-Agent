# 用目标系统的共享库恢复原 APK 行为

用开发提交 `1a7bbbe`、团队自有归档阅读器和 Android 16 API 36 x86_64 专用模拟器验证两条路径。

- 脚本依次调用原包测试、界面操作、系统库声明、构建及行为预检。原包点击后缺少 `org.apache.http.ProtocolVersion`，目标系统列有 `org.apache.http.legacy`。回编后在二进制清单中核实为可选库声明。应用权限、组件和解包后的两份 smali 文件保持一致，回编后的 DEX 容器字节有变化。预检和另行独立验收各通过 6 步，候选与设备安装回读哈希一致。[设备核对](agent-platform-library-device.json)
- DeepSeek Flash 从同一原包开始，自主复现崩溃、调用库声明工具并生成一个候选。修改记录只有 `AndroidManifest.xml`。工作台记录 12 次模型响应、一次构建和 177.6 秒。Agent 预检通过 6 步，随后另行独立验收通过 6 步。[模型运行摘要](agent-platform-library-model.json)

两条路径使用已见样本。样本调用固定参数的协议版本对象，结论覆盖旧系统库兼容和保留该应用原行为。第三方真实归档文件及其他系统版本需要各自的输入和验收。
