# 连续编辑专项中的文件交接候选

源码 `9149285f01e1708ff69aa83c51fccf66b06c2258` 的专项沿用固定 P1C 样本与设备断言。两份候选已在 Android 16 x86_64 专用模拟器通过安装、启动和五步功能验收。APK 文件哈希与设备安装回读一致。该记录为开发分支专项，v0.6.24 交付记录仍对应原构建。

| 轮次 | 实现差异 | 外部输入复核 |
| --- | --- | --- |
| P06-agent-1 | 显式发送自定义 URI，接收页依据 scheme、host、path 读取固定记录文件。清单保留导出的接收页。 | 使用 `ggbond-record://local/record.txt` 比较同一外部 UID 在写入前后的结果。 |
| P06-agent-2 | 显式发送内容 URI，新增只读 Provider，接收页按 authority 和路径筛选。接收页仍导出。 | 使用 `content://org.ggbond.archivereader.records/record` 进行相同对照。 |

应用内五步覆盖冷启动前后的合法交接。外部探针将另行核对已导出接收页能否被独立 UID 触发，两份候选暂保留在审查区。[候选和设备哈希](agent-edit-revision-p06-observed.json) · [完整十二轮专项](agent-edit-revision-focused.md)
