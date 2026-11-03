# Lazarus Agent

Windows 桌面版「遗留软件复活智能体」。导入旧 APK，选择 Android 14、15 或 16，在隔离模拟器中安装、运行并验证修复结果。复测问题、截图、可重复操作和独立 APK 版本保存在本机。

## 启动

从 [Releases](https://github.com/RoamerFly/Lazarus-Agent/releases) 下载 Lazarus-Agent.exe。桌面程序自带 Python 和 Web 服务，用 Microsoft Edge 的独立应用窗口显示界面。运行 APK 或生成修复包还需要 Android SDK、对应的模拟器镜像和 JDK。启动后点击“修复记录”可恢复历史任务。

设置 `REVIVER_SDK` 指向 Android SDK 根目录（包括 platform-tools、emulator、build-tools），按需设置 `REVIVER_BUILD_TOOLS`、`REVIVER_LAB`。默认本地端口为 8767，可通过 `REVIVER_PORT` 调整。任务数据位于 `%LOCALAPPDATA%\LegacyApkReviver\jobs`。初次使用前见[运行指南](运行指南.md)。若桌面版与旧网页服务使用同一端口，请先退出旧服务。

## 反馈后继续修复

1. 导入 APK，选择目标系统，点击“修复并验证”。通过验证的文件进入版本列表，保留当时的验证等级。
2. 在手机上测试，选择对应版本，点击“真机可运行”，或描述问题并附一张 PNG/JPEG 截图。
3. 如能复现，请在“设置复现步骤”填写点击的控件和预期内容。也可以发送形如「点击“Open archived record”后应显示“Read OK: HTTP/1.1”」的消息。
4. 点击“继续修复”。工作台依据本轮运行证据选择已实现的规则。通过验证且内容变化时新增版本；相同哈希会沿用原版本。截图作为本机反馈记录，当前规则引擎使用可观测的安装、启动、控件和运行时证据进行判断。

代码仓库包含团队自制测试 APK。第三方商业游戏、实验室虚拟机、私有签名密钥和用户对话不入库。《环太平洋》的完整游戏交互复核需要事先配对的 ARM 实验室和本地参考画面；可通过 `REVIVER_GAME_REFERENCES` 指定参考目录。原开发机的验证范围见[阶段进度](阶段进度.md)。

## 开发和打包

```powershell
.\start_workbench.ps1
# 构建单文件 Windows 程序
.\build_desktop.ps1
```

输出路径为 `dist\Lazarus-Agent.exe`。源码运行与桌面程序共用工作台 API 和本地任务格式。
