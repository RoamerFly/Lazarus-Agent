# 离线 Android 验证资源包

## 默认的按需准备（发布版不携带整套镜像）

发布包默认不含 SDK、Java 或 system image。用户确认 Android SDK / Temurin 的发布方条款后，
由工作台仅为选中的 API（34、35 或 36）准备所需组件。准备器优先用**只读 junction**复用已安装
的 SDK、镜像和 Java；缺少时才从官方 catalog 下载，校验发布方 SHA-1/SHA-256，安全解压至 staging
并原子发布到 `%LOCALAPPDATA%\Lazarus-Agent\runtimes\android-lab`。它不修改 Android Studio SDK、
旧实验室或正在运行的 AVD；取消/超时时保留 `.part` 文件，后续可续传。

```powershell
python .\tools\prepare_lab.py inventory --api 36
python .\tools\prepare_lab.py prepare --api 36 --accept-sdk-terms
```

后端入口为 `tools.prepare_lab.inventory(api)` 和
`tools.prepare_lab.prepare_runtime(api, progress, cancel=..., timeout_seconds=..., terms_accepted=True)`。
准备完成会刷新当前进程已加载的 `bench_device.SDK`、`ADB`、`TOOLS`，但不会启动模拟器。

## 可选的本机离线资源包

桌面程序的 APK 解包、重签名、对齐和 AVD 验证不依赖系统 `PATH`。正式演示机可在
EXE 同级放置本机资源包：

```text
dist/
  Lazarus-Agent.exe
  Lazarus-Agent-Offline-Lab-Windows-x64/
    android-sdk/       # adb、emulator、build-tools 与 API 34/35/36 AOSP 镜像
    jre/               # java + keytool
    manifest.json      # 所有文件 SHA-256
```

在已接受 Android SDK 许可、且已经下载镜像的电脑上组装：

```powershell
python .\offline_kit.py build `
  --sdk "E:\Software\Android-Tool\Sdk" `
  --image-sdk "$env:LOCALAPPDATA\LegacyApkReviver\lab-sdk" `
  --java-home "E:\A-Environment\Language\Java-ALL\Java\jdk-1.8"
python .\offline_kit.py verify --out .\dist\Lazarus-Agent-Offline-Lab-Windows-x64
```

也可在生成 EXE 后一次性组装（两类 SDK 在本机分离时必须同时指定）：

```powershell
.\build_desktop.ps1 -BuildOfflineKit -Sdk "E:\Software\Android-Tool\Sdk" `
  -ImageSdk "$env:LOCALAPPDATA\LegacyApkReviver\lab-sdk" `
  -JavaHome "E:\A-Environment\Language\Java-ALL\Java\jdk-1.8"
```

该目录是**本机资源包**：不得加入 Git、公共 GitHub Release 或对外参赛 ZIP。Google 的
Android SDK 和 system image 的使用及再分发受其 SDK 许可约束；公开仓库只保留此脚本、
清单格式和发现逻辑。不同机器应由各自许可持有人重新下载并组装。

程序发现顺序为：`REVIVER_OFFLINE_KIT` → EXE 同级资源包 → 受控用户目录
`%LOCALAPPDATA%\Lazarus-Agent\runtimes\android-lab` → 旧版受控目录 → 现有 SDK。
因此即使清空 `PATH`、未安装 Android Studio，只要同级包存在，也可运行 APK 分析、签名与
API 34/35/36 x86_64 AVD 验证。ARM 专用实验环境仍是本机私有附加资源，不包含在该包中。

若资源包或 EXE 位于中文目录，启动脚本会在 ASCII 临时目录创建受控 junction，并让 SDK、
AVD、`config.ini` 与 `ANDROID_AVD_HOME` 全部通过该路径访问。便携/显式存储模式不会复制
既有安装的签名私钥；它会生成该便携实例独有的本地签名身份。
