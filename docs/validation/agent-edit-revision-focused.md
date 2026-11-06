# 连续编辑修复的十二轮专项回归

专项使用源码 `9149285f01e1708ff69aa83c51fccf66b06c2258`，从冻结 P1C 日程选取记录阅读器和文件交接。原 APK、自然语言需求、Android 16 x86_64 目标、独立验收步骤及三组对照沿用冻结输入。十二轮均有终态。

| 用例 | Agent | 无修改回编 | 离线规则 | 单次构建 |
| --- | --- | --- | --- | --- |
| 记录阅读器 P05 | 3/3 | 0/1 | 1/1 | 0/1 |
| 文件交接 P06 | 2/3 | 0/1 | 0/1 | 0/1 |

P05 的三个 Agent 候选保留原记录资源。第一份在成功读取后保存文件名，另两份在读取前保存用户选择，固定 A/B 场景均完成八步。P06 两份候选的应用内五步通过，导出的接收页仍需外部探针复核。最后一轮达到执行上限，按未观测保留在分母。前一版 P06 三轮均未导出可验收候选，本专项提供开发对照，模型试验的随机差异仍需保留。

审计器可复核选定用例、源码提交、协议、APK 输入及安装哈希、完整终态和设备步骤。错误轮次数、重复用例、改动协议哈希及错版源码均被拒绝。专项不计入全组发布门槛，分支尚未构建桌面 EXE。

    python tools/audit_f01_release_regression.py <源码检出目录> <检出目录内的原始结果> docs/validation/agent-edit-revision-focused-plan.json --focused

[专项计划](agent-edit-revision-focused-plan.json) · [专项计分](agent-edit-revision-focused.json) · [六份通过产物的编译与差异记录](agent-edit-revision-focused-static.json)
