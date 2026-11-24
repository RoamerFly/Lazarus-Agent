# dist/env 便携交付验证

2026-11-24，本地源码构建。EXE SHA-256：`ad4c677dcab803a6bb5f86497e3b759b2e4d30dd1999dd796964ef2c702a1c77`。

- env 包含 1261 个文件，4445384925 字节，API 36 x86_64 镜像，以及 SDK、Java、固定版 WebView2、Apktool。
- SDK、Java 和 WebView2 均为真实文件，env 目录未发现 junction/reparse point。
- 固定版 WebView2 158.0.4370.33 x64 来自 Microsoft 官方下载页，主可执行文件 Authenticode 为 Valid、Microsoft Corporation。
- 清空 REVIVER/LAZARUS 配置、Java/Android 路径，PATH 仅留 Windows System32，使用全新临时任务存储。EXE self-test 退出 0；Java、ADB、Apktool 均退出 0，实际发现路径均在 env 内。
- 启动一次正式工作台，desktop_protocol=1、editing_ready=true、targets.36=true；观察到 env 内固定版 WebView2 的实际子进程。未调用在线模型。
- 将完整 dist 临时移动到含中文与空格的 build/portable moved 环境，再次验证全部文件哈希和 EXE/工具自检均通过，随后恢复 dist。
- 单元测试 191 passed，1 个原有 ZIP 重复成员警告。

本次是本机隔离 PATH/配置及真实目录迁移检查，不是新 Windows 系统实机验收。CPU 虚拟化、Windows .NET Framework 和系统虚拟化支持仍由目标操作系统提供；x86_64 镜像不覆盖 ARM32 游戏。

原始本地回执：build/portable-env-verification.json、build/portable-env-relocation.json、build/portable-ui-verification.json。重跑命令：`python tools/verify_portable_env.py`。
