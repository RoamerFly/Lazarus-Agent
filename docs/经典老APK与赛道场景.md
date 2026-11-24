# 经典历史 APK：F01 场景样本

2026-11-24 搜集。F01 赛道面向科研生产、经营管理的实际转型问题；本项目将历史工具用于“终端升级后，恢复存量工具并按实际需求改造”的演示。以下应用与场景的对应关系是项目的选题建议，不代表已在中国电科落地。

APK 来自 F-Droid 官方 archive，逐个核对官方索引大小、SHA-256、ZIP 完整性，并使用 SDK 的 aapt 和 apksigner 读取清单、验证签名。完整下载地址、证书摘要、源码链接、版本日期及许可见 [机器可读目录](classic-apk-catalog.json)。日期为历史仓库收录日期，不冒充开发者发布日期。

本次收集的是历史版本，有的项目至今仍在维护。2026-11-24 已在 Android 16/API 36 x86_64 专用模拟器实测全部六款原包：前五款被默认安装策略拒绝，phyphox 可安装并显示应用界面。完整实验和修复效果尚未验证，详见 [Android 16 原包实测](validation/classic-apk-android16.md)。phyphox 作为可安装启动的对照，不作为已知无法运行的样本。

| 样本 | 对应场景 | 可设计的需求与验收 | 优先级和实际限制 |
|---|---|---|---|
| Sensor Readout 2.4 | 传感器检查、试验台读数、不同终端传感器对比 | 恢复安装；确认实际数据来源后保存带试验编号的读数 | 第一优先，体积小、无原生 ABI 负担；真实精度与传感器能力需真机核验 |
| ODK Collect 1.2.2 | 离线巡检表、设备点检、现场试验记录 | 导入本地表单、填写并保存、重启读回；检查原功能后按需要增加单位/编号校验 | 第一优先，业务场景清楚；旧存储路径和表单管理可能需要进一步修改 |
| WiFiAnalyzer 1.4.2.33 | 厂区/实验室无线网络排查、信道与信号强度观察 | 恢复启动、整理扫描列表；核对当前版本功能后设计筛选或记录需求 | 第二优先，新系统 Wi-Fi 扫描权限和频率限制需单独处理；模拟器结果不代表真实射频测量 |
| GPSLogger 27-fdroid | 外场测试路线、巡检轨迹、测点位置记录 | 开始记录、停止并导出轨迹；对导出命名或路线备注提出需求 | 第二优先，需定位输入；模拟器注入轨迹与真机卫星定位应区分，后台定位限制较多 |
| Open Camera 1.32 | 现场照片留证、设备外观记录、试验照片采集 | 拍照、保存、重开确认文件；检查已有功能后提出任务编号或归档命名需求 | 第二优先，需要摄像头和存储验证；模拟器摄像头不能证明真机拍照质量 |
| phyphox 1.1.0 | 手机传感器物理实验、振动与声学教学/试验、数据导出 | 选择已有实验、采集、导出并核对数据；也可作“本来能运行”的对照样本 | 科研相关性高，已有 x86_64/ARM64 库；目标 API 较高，不预判存在安装门槛故障 |

## 来源

- [F01/赛事赛道说明](https://wangxinbei.taiji.com.cn/tracks)
- [Sensor Readout 开发者介绍](https://www.onyxbits.de/sensorreadout) · [源码](https://github.com/onyxbits/sensorreadout)
- [ODK Collect 官方介绍](https://docs.getodk.org/collect-intro/) · [源码](https://github.com/getodk/collect)
- [WiFiAnalyzer 仓库条目](https://f-droid.org/en/packages/com.vrem.wifianalyzer/)
- [GPSLogger 仓库条目](https://f-droid.org/en/packages/com.mendhak.gpslogger/)
- [Open Camera 仓库条目](https://f-droid.org/en/packages/net.sourceforge.opencamera/)
- [phyphox 官方源码](https://github.com/phyphox/phyphox-android)

## 使用方式

下载文件位于源码 old_apk 和便携版 dist/old_apk。便携用户打开 EXE，导入其中一个 APK，选择 Android 16 后先运行原包或修复并验证。需要新增行为时再启用模型并输入具体需求。上表中的改造是待验证目标，必须先分析原包是否已经具有该功能。

优先用 Sensor Readout 验证轻量兼容修复，再用 ODK Collect 验证离线记录业务闭环。真实定位、摄像头、Wi-Fi 和测量精度需要实际硬件，界面与安装验证分别记录。对外分发原包或修改版时，应同时保留许可、版权与相应源码要求。
