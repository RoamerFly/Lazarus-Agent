"""Render the current F01 proposal from published validation summaries."""
from pathlib import Path
import json

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "交付/遗留软件复活智能体方案说明.pdf"
TEAL, INK, MUTED = colors.HexColor("#08786f"), colors.HexColor("#15363c"), colors.HexColor("#547177")
PAGE_WIDTH, PAGE_HEIGHT = A4
CONTENT_WIDTH = PAGE_WIDTH - 104


def build():
    from app_version import APP_VERSION

    for name, filename in (("Song", "simsun.ttc"), ("Hei", "simhei.ttf")):
        pdfmetrics.registerFont(TTFont(name, "C:/Windows/Fonts/" + filename, subfontIndex=0))
    body = ParagraphStyle("body", fontName="Song", fontSize=11.4, leading=19,
                          spaceAfter=12, wordWrap="CJK", textColor=INK)
    title = ParagraphStyle("title", parent=body, fontName="Hei", fontSize=24, leading=33, spaceAfter=20)
    sub = ParagraphStyle("sub", parent=body, fontName="Hei", fontSize=14.5, leading=23,
                         spaceBefore=14, spaceAfter=8, textColor=TEAL)
    tiny = ParagraphStyle("tiny", parent=body, fontSize=9.2, leading=14, spaceAfter=5)
    tag = ParagraphStyle("tag", parent=tiny, fontName="Hei", textColor=TEAL, spaceAfter=18)
    story = []
    validation = ROOT / "docs/validation"
    evidence_path = validation / "f01-p1-results.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.is_file() else None

    def para(text, style=body):
        return Paragraph(text, style)

    def heading(kicker, text):
        story.extend((para(kicker, tag), para(text, title)))

    def block(name, text):
        story.append(KeepTogether([para(name, sub), para(text)]))

    def table(headers, rows, widths):
        data = [[para(escape(str(value)), tiny) for value in row] for row in [headers, *rows]]
        result = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
        result.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e1f2ef")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#b8d4d0")),
            ("LINEBELOW", (0, 0), (-1, 0), .6, TEAL),
            ("LINEBELOW", (0, 1), (-1, -1), .3, colors.HexColor("#dce7e5")),
            ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.extend((result, Spacer(1, 12)))

    windows_proof = validation / f"{APP_VERSION}-windows-proof.json"
    heading("F01 数智创意 / 工程复核版" if windows_proof.is_file() else "F01 数智创意 / 工程候选稿",
            "遗留软件复活智能体")
    story.append(para("从一份旧 APK 和一句需求开始，交付能在目标 Android 上复测的应用。", sub))
    story.append(para("面向接手遗留安卓工具的维护人员。用户导入 APK，指定目标系统和需要完成的操作，Agent 自主分析包内代码、资源与界面，修改并回编签名。在独立环境中，用预先提交的步骤验证原包与候选，保留每轮差异、哈希和失败原因。"))
    block("拟解决的问题", "换新终端后，旧应用可能无法安装，也可能在某个业务操作后崩溃。原工程暂不可用时，维护人员仍有安装包、业务经验和操作规程。兼容调整之后，班组还可能需要记住上次工作位置、调整流程或修改显示。仅交付一个可打开的包，无法覆盖这些操作。")
    block("具体使用场景", "本版用自有离线巡检单模拟维护班组换新平板。原包 targetSdk 为 22，在 Android 16 默认安装被拒绝。需求同时要求恢复使用，并在再次启动时保留上次选中的路线和原巡检内容。模拟数据包含南线、北线及两条巡检说明。")
    table(["使用者提供", "系统交付"], [
        ["旧 APK、目标 Android、业务需求", "签名 APK、修改差异、每版来源"],
        ["预期操作和可观察结果", "逐系统安装及行为报告、哈希复核入口"],
    ], [CONTENT_WIDTH * .48, CONTENT_WIDTH * .52])
    story.append(para("重庆邮电大学 · 重邮GGBond · 刘飞扬<br/>Windows 桌面程序、公开源码、自有可分发样本与复测指南。", tiny))
    story.append(PageBreak())

    heading("技术路线", "从需求到可复测交付")
    table(["环节", "实际执行与证据"], [
        ["1. 固定输入", "记录 APK 哈希、包名、SDK 与 ABI。提交需求时固定目标系统和独立验收步骤。"],
        ["2. 模型分析", "提供受限文件列表、文本搜索、分段读取和 smali 方法读取。模型选择需要的证据，文件内容与历史反馈作为数据处理。"],
        ["3. 修改与回编", "精确修改代码和资源，也可新增 smali、资源或 assets。Apktool 回编、zipalign 对齐、apksigner 签名并校验。"],
        ["4. 设备诊断与预检", "模型可先运行原包复现故障，按设备证据声明可选系统库。构建后可重放固定步骤，依据首个失败点继续修订。"],
        ["5. 独立验收", "另行安装原包与候选，核对设备回读 SHA-256，按提交时的固定步骤执行。只匹配目标应用自身控件。"],
        ["6. 交付迭代", "通过所选系统的功能检查后提供已验收 APK。候选、历史版本、报告和差异保留；用户反馈可基于上版继续。"],
    ], [94, CONTENT_WIDTH - 94])
    block("三种完成状态", "构建签名通过证明包已生成；安装启动通过证明本次环境能够启动；指定行为通过证明固定操作在该环境达到预期。未指定步骤时只记录启动验证。一个目标失败时，保留试验候选与逐项原因。")
    block("成功候选的保存", "后续试验失败时，只在文件集合与内容哈希精确回到某次成功构建时复用该候选。其安装与行为仍由验收器确认。每轮保留原始工具轨迹，限制目录、文件大小、工具调用与构建次数。")
    story.append(PageBreak())

    heading("工程创新与范围", "开放需求、受限执行、独立验收")
    block("项目自研部分", "需求对话与 APK 工作区的通用编排；有哈希前置校验的精确编辑；构建、试验、回滚与多轮版本关系；模型说明之外的行为断言执行；下载状态与实际安装字节绑定；按需准备环境及桌面交付。")
    block("采用的基础工具", "外部模型承担分析和代码生成，Apktool、Android SDK 工具与隔离运行环境承担解包、回编、签名和运行。模型可以按需调用已有资源工具。外部模型输出本身不能确定候选是否通过功能验收。")
    library_path = validation / "agent-platform-library-model.json"
    if library_path.is_file():
        trial = json.loads(library_path.read_text(encoding="utf-8"))
        block("已见应用的设备证据", f"归档阅读器点击后因缺少旧 HTTP 类而崩溃。Agent 自主复现后只添加可选库声明，构建 {trial['build_attempts']} 次，"
              f"预检和独立验收各通过 {trial['independent_steps_passed']} 步。原包无真实归档文件，验证范围为恢复样本原有操作。")
    table(["对标", "对应能力与本项目侧重"], [
        ["APKLab", "提供 APK 编辑工具链集成。本项目侧重自然语言驱动的自主修改，以及候选版本和独立行为证据的关联。"],
        ["Apktool", "提供资源和代码的解包回编。本项目复用其构建能力，在其上实现任务、试验与交付流程。"],
        ["DroidBot", "提供界面输入测试。本项目按用户提交的固定断言核查原包与候选的行为差异。"],
    ], [80, CONTENT_WIDTH - 80])
    block("当前边界", "已覆盖单个 APK 的文本资源、Manifest、smali 与部分资产改造。缺失的目标架构机器码、停服后台、受保护或无法理解的逻辑都可能形成阻碍。自绘画面、联网结果和业务真实性需要相应的额外证据。已测样本和 Android/ABI 范围逐项列在报告。")
    story.append(para('技术对标来源　<a href="https://github.com/APKLab/APKLab" color="#08786f">APKLab</a>　<a href="https://apktool.org/" color="#08786f">Apktool</a>　<a href="https://github.com/honeynet/droidbot" color="#08786f">DroidBot</a>', tiny))
    story.append(PageBreak())

    heading("固定评测", "重复、对照与失败一起报告")
    story.append(para("预先冻结 6 项正例各 3 轮、3 项反例各 2 轮，另对每项正例执行无修改回编、旧规则和同模型单次构建各一次，共 42 轮。需求、断言、样本及运行器均有冻结记录。独立应用数量和同任务的重复轮次分别统计。"))
    table(["用例", "预设核验内容"], [
        ["巡检单", "恢复安装，南北路线切换后冷启动保留选择及原说明。"],
        ["归档阅读器", "读取操作中的旧 HTTP 依赖故障，修复后能完成原操作。"],
        ["计时器 / 耗材清单", "改写资源内容并保留计数和点击后的行为。"],
        ["文件阅读器", "选中文件后冷启动仍显示同一文件的原始内容。"],
        ["本地分享", "修复文件 URI 交接，接收页面真实读取记录。"],
        ["反例", "MIPS-only JNI、从未存在于输入的校验码、前后矛盾的界面断言。"],
    ], [114, CONTENT_WIDTH - 114])
    batches = [("第一批", evidence)]
    for label in ("P1B", "P1C"):
        path = validation / f"f01-{label.lower()}/results.json"
        if path.is_file():
            batches.append((label, json.loads(path.read_text(encoding="utf-8"))))
    score_rows, control_rows = [], []
    for label, result in batches:
        if not result:
            continue
        counts = result["counts"]
        gate = result.get("joint_gate", counts["agent_positives"] >= 15 and counts["primary"] == 3
                          and counts["agent_negative_rejected"] == 6 and counts["agent_false_acceptances"] == 0)
        held_out = f"{result['held_out_passes']}/3" if "held_out_passes" in result else "无"
        score_rows.append([label, f"{counts['agent_positives']}/18", f"{counts['primary']}/3",
                           f"{counts['agent_negative_rejected']}/6 · {counts['agent_false_acceptances']}/6",
                           held_out, "达到" if gate else "未达到"])
        control_rows.append(f"{label} {counts['roundtrip_positive_cases']}/6、{counts['rules_positive_cases']}/6、"
                            f"{counts['one-build_positive_cases']}/6")
    if score_rows:
        table(["批次", "正例", "主案例", "反例拒绝 / 误验收", "留出", "联合门槛"], score_rows,
              [48, 47, 48, 128, 44, CONTENT_WIDTH-315])
        story.append(para("对照按无语义改动回编、原规则、同模型一次构建排序。"
                          + "。".join(control_rows) + "。第一批一次构建有一轮中断，完整分母保留。", tiny))
    if not any(label == "P1C" for label, _ in batches):
        story.append(para("P1C 已冻结 42 轮并启动，加入仓库温度卡，检查换算与冷启动保留单位。结果待归档。", tiny))
    story.append(para("已见任务另有全组开发回归和主案例回归。同一候选在 Android 14、15、16 上通过固定步骤。OI Notepad 的两轮修改有独立哈希链。", tiny))
    story.append(PageBreak())

    heading("部署与试点", "给复核人员一条可重复的路径")
    block("Windows 安装", "提供按用户安装器和便携 ZIP。桌面内嵌 WebView2，模型凭据由 Windows DPAPI 加密。程序优先复用工具，首次按所选系统准备缺失的 SDK、Java 与镜像。后续可在已准备环境中断网复测已有候选。")
    replay_path = validation / f"{APP_VERSION}-windows-proof.json"
    replay_note = f"v{APP_VERSION} 干净 Windows 候选复测待归档。"
    if replay_path.is_file():
        replay = json.loads(replay_path.read_text(encoding="utf-8"))
        preparation = replay["summary"]
        count = len(replay["acceptance"]["steps"])
        runs = [replay, *(run for run in replay.get("repeat_runs", []) if run.get("pass"))]
        durations = [run["summary"]["duration_seconds"] for run in runs]
        times = f"{min(durations):.0f}" if len(runs) == 1 else f"{min(durations):.0f} 至 {max(durations):.0f}"
        replay_note = (f"v{APP_VERSION} 干净 Windows 首次准备约 {times} 秒。"
                       f"正式 EXE 的巡检单 {count} 步复测通过 {len(runs)} 轮，占用约 {preparation['bytes']/1024**3:.2f} GiB。")
    cold_path = validation / f"{APP_VERSION}-cold-start-observation.json"
    if cold_path.is_file():
        cold = json.loads(cold_path.read_text(encoding="utf-8"))
        count = 1 + len(cold.get("repeats", []))
        cold_note = f"固定候选冷启动 {count} 轮未观测到目标页面。"
        replay_note = replay_note + "另有" + cold_note if replay_path.is_file() else cold_note
    local_rule_path = validation / f"{APP_VERSION}-local-rule-windows.json"
    local_rule_note = "本版关闭模型并从原包生成 APK 的结果待独立核验。"
    if local_rule_path.is_file():
        local_rule_note = f"v{APP_VERSION} 在干净 Windows 关闭模型，用本地规则生成新 APK 并通过指定操作。"
    elif (validation / f"{APP_VERSION}-local-rule-observation.json").is_file():
        local_rule_note = "本地规则已生成、签名和安装候选，指定操作待通过复验。"
    installer_path = validation / f"{APP_VERSION}-installer-check.json"
    installer_note = ("正式安装器通过自检、输入哈希、篡改拒绝和卸载检查。" if installer_path.is_file()
                      else "本版正式安装器验证待归档。")
    if not installer_path.is_file() and (validation / f"{APP_VERSION}-candidate-installer-check.json").is_file():
        installer_note = "签名候选安装器已在干净 Windows 通过安装、自检、篡改拒绝和卸载检查。"
    outbound_note = "复测限制所列程序的 Internet 出站，保留本机通信。" if replay_path.is_file() else ""
    block("当前独立机器证据", installer_note + replay_note + outbound_note + local_rule_note +
          "此前的冷启动观察与中文路径失败另有记录。非开发者试用待补充。")
    block("资源与费用", "系统镜像是主要下载和存储项，默认安装包不随包分发全部镜像。每轮记录耗时、工具与构建请求、模型输入输出和缓存计量；缺少计量时保留空值。模型费用、环境准备耗时与人工复核分别记录，实际现场成本以试点测量为准。")
    block("拟定试点", "从授权的离线应用和合成业务记录开始，明确维护员、操作员与独立复核员。先完成关键流程及数据备份，再测设备更新、文件迁移与回滚。比较拿到 APK 到通过关键操作的用时、人工次数和失败恢复用时。当前巡检单是模拟场景，尚无企业试点收益数据。")
    table(["路径", "进入下一步的证据"], [
        ["第 1 年 / 可复核原型", "公开用例与拒绝分支，主流程可独立复跑。"],
        ["第 2 年 / 小规模试点", "授权样本、现场卡点、数据迁移及回滚记录。"],
        ["第 3 年 / 扩展设备", "更多框架与 ABI 的固定回归，权限和签名变化审查。"],
        ["第 4 至 5 年 / 持续维护", "稳定的案例库、工具链锁定及独立团队复核。"],
    ], [136, CONTENT_WIDTH - 136])
    story.append(PageBreak())

    heading("交付与团队", "按证据定位每一份成果")
    block("单人团队", "参赛队伍为重邮GGBond，负责人刘飞扬，所在单位重庆邮电大学。由负责人承担场景确定、需求与验收、工具集成及参赛材料整理。代码实现和文档制作使用 AI 辅助，模型服务与开源工具的作用在实现和依赖说明中列出。")
    table(["交付物", "复核重点"], [
        ["Windows 程序与签名更新清单", "版本、发布提交、文件 SHA-256 与更新清单签名。"],
        ["源码与自有样本", "可分发输入 APK、构造样本源码、构建入口及使用说明。"],
        ["APK、差异与验收报告", "原包、候选及设备回读哈希，目标 Android/ABI，逐步操作结果。"],
        ["固定评测与对照", "全部预定轮次、失败及中断、模型响应计量、人工差异审查。"],
        ["演示和运行指南", "导入、自然语言需求、工具执行、结果交付和反例。"],
    ], [143, CONTENT_WIDTH - 143])
    block("材料状态与完成条件", "评审包按当前清单制作，文件哈希可以独立计算。部署复核、行为验收和非开发者试用分别给出实际证据。缺失的记录在完成清单中列为待完成，计划日期用于内部推进。")
    story.append(para('公开源码　<a href="https://github.com/RoamerFly/Lazarus-Agent" color="#08786f">RoamerFly / Lazarus-Agent</a><br/>'
                      '赛题来源　<a href="https://wangxinbei.taiji.com.cn/track/36" color="#08786f">中国电科“网信杯” F01</a><br/>'
                      '配套文件　F01-参赛优化与验收计划、固定评测协议、独立验收报告、部署复核说明。', tiny))

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(TEAL)
        canvas.rect(0, PAGE_HEIGHT - 9, PAGE_WIDTH, 9, stroke=0, fill=1)
        canvas.setFont("Hei", 9)
        canvas.setFillColor(MUTED)
        canvas.drawString(52, PAGE_HEIGHT - 34, "中国电科网信杯  /  F01 数智创意")
        canvas.drawRightString(PAGE_WIDTH - 52, PAGE_HEIGHT - 34, "Lazarus Agent")
        canvas.setStrokeColor(colors.HexColor("#d0e1de"))
        canvas.line(52, 44, PAGE_WIDTH - 52, 44)
        canvas.setFont("Song", 8.5)
        canvas.drawString(52, 29, f"重庆邮电大学 · 重邮GGBond   |   工程版本 {APP_VERSION}")
        canvas.drawRightString(PAGE_WIDTH - 52, 29, str(doc.page))
        canvas.restoreState()

    OUT.parent.mkdir(exist_ok=True)
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, rightMargin=52, leftMargin=52,
                            topMargin=66, bottomMargin=60, title="遗留软件复活智能体方案说明",
                            author="重庆邮电大学 · 重邮GGBond")
    doc.build(story, onFirstPage=page, onLaterPages=page)
    print(OUT)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    build()
