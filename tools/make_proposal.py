"""Render the F01 review proposal from the validated sample reports."""
from pathlib import Path
from datetime import datetime
import json
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph

ROOT=Path(__file__).resolve().parents[1]
PEER=ROOT/"evidence/peer-web"
OUT=ROOT/"交付/遗留软件复活智能体方案说明.pdf"
OUT.parent.mkdir(exist_ok=True)
for alias,file in [("Song","simsun.ttc"),("Hei","simhei.ttf")]:
    pdfmetrics.registerFont(TTFont(alias,"C:/Windows/Fonts/"+file,subfontIndex=0))
c=Canvas(str(OUT),pagesize=A4)
c.setTitle("遗留软件复活智能体方案说明")
c.setAuthor("重庆邮电大学 · 重邮GGBond")
W,H=A4
L=48;R=W-48
ink="#183238"; muted="#49696C"; teal="#056355"
def label(x,y,s,size=11,font="Hei",color=ink):
    c.setFillColor(HexColor(color));c.setFont(font,size);c.drawString(x,y,s)
def bar(y):
    c.setStrokeColor(HexColor("#BCCFC7"));c.setLineWidth(.6);c.line(L,y,R,y)
def paragraph(s,top,size=12,leading=21,width=None,x=L):
    style=ParagraphStyle("body",fontName="Song",fontSize=size,leading=leading,wordWrap="CJK",textColor=HexColor(ink))
    p=Paragraph(s,style);_,h=p.wrap(width or R-L,700);p.drawOn(c,x,top-h);return top-h
def header(n,section,title):
    label(L,796,"中国电科“网信杯”人工智能创新大赛",11)
    label(R-102,796,"F01  /  数智创意",12,color=teal);bar(782)
    label(L,750,section,11,color=teal);label(L,711,title,24)
    bar(686)
    label(L,32,"重庆邮电大学    /    重邮GGBond    /    2026.11",9,color=muted)
    label(R-34,32,f"{n}  /  4",9,color=muted)
def block(title,body,y):
    label(L,y,title,15,color=teal);return paragraph(body,y-12)-31
def refs(items,y=65):
    x=L
    for title,url in items:
        label(x,y,title,9,color=teal)
        w=pdfmetrics.stringWidth(title,"Hei",9);c.linkURL(url,(x,y-2,x+w,y+11));x+=w+18
def end(y):
    assert y>53,y
    bar(49);c.showPage()
def report(stem):return json.loads((PEER/(stem+"-report.json")).read_text(encoding="utf-8"))
oi=report("oi-api35"); ar=report("archive-broken-api35")
def elapsed(rep):
    times=[datetime.fromisoformat(e["time"]) for e in rep["events"]]
    return round((max(times)-min(times)).total_seconds())
header(1,"作品方案  /  报名队伍负责人 刘飞扬","遗留软件复活智能体")
y=paragraph("用户手里只剩安装包，却需要在新设备上继续使用一款旧工具。本项目面向接手这类软件的维护人员。用户导入 APK、选择 Android 版本并指定一个需要保留的操作，工作台尝试恢复该操作，最后提供可以独立安装的 APK 和验证记录。",660,14,24)-36
y=block("拟解决的问题","维护人员往往能看到“无法安装”，却不知道是系统门槛还是处理器架构不符。有些程序可以打开，点击读取文件时才崩溃。如果验收止于启动画面，这种故障会在交付后再次出现。",y)
y=block("使用场景","研发测试岗位需要在终端换代后复核历史工具的一个关键步骤，例如读取归档文件。工作台在测试环境中复现原始错误，保留修复差异，再以原操作核验。企业试点可先从内部授权、能够离线复测的一款工具开始。",y)
y=block("本版成果","已在 Windows 电脑上实现安卓工作台。旧记事本在 Android 14 和 15 上的安装门槛已修复，并在四个干净模拟器实例中完成笔记保存与重开验证。团队另构造了一个运行时故障，验证修复流程能否处理安装后的功能崩溃。",y)
y=block("研发阶段","当前演示由本地诊断规则驱动。模型适配器已实现，本轮按项目选择关闭在线模型调用。模型辅助判断的增益、真实企业部署和物理手机兼容性均待后续试验。",y)
end(y)
header(2,"技术实现  /  从输入包到验证结果","修复与验收流程")
y=block("01   建立原包和目标设备的基线","记录 APK 哈希、签名证书、包名、最低及目标 API、原生库架构。读取目标安卓的实际 API 和 ABI 列表。用户可以在专用模拟器中直接运行原包，也可以预览旧环境。默认安装返回的错误码和本次执行步骤随任务保存。",657)
y=block("02   把故障证据对应到可复查的修改","原包因目标 API 过低被拒，且无已知的架构阻断时，执行器修改编译后清单中的目标值。遇到功能点崩溃时，检查该应用进程的异常和系统可用库；只有观察到缺失的旧 HTTP 类且系统提供对应共享库时，才写入库声明。修改后重新对齐和签名，保留输入包。",y)
y=block("03   重做用户关心的动作","记事本验收使用每次新生成的文本。其他应用可以指定要点击的控件及预期内容。验证记录包含包的安装与启动结果、界面树和截图。选定的操作失败时，报告保留失败证据；仅做启动检查时，界面注明功能待核验。",y)
y=block("04   交付给下一位维护人员","交付 APK、哈希和可下载的任务报告。已兼容的应用通过验证后可导出原包，保留原签名。修改包使用本地测试证书，需全新安装。权限、签名依赖、处理器架构或已停服的后端都可能限制恢复范围。",y)
refs([("Android 安装规则","https://developer.android.com/about/versions/15/behavior-changes-all"),("Android 共享库说明","https://developer.android.com/about/versions/pie/android-9.0-changes-28"),("Android 应用签名","https://developer.android.com/studio/publish/app-signing")])
end(y)
header(3,"实测证据  /  Windows 主机与安卓模拟器","两类修复与对照")
y=block("真实历史应用   OI Notepad 1.3","2012 年版本的开源记事本目标 API 为 15。在 Android 15 上默认全新安装时，系统日志明确要求至少为 24。对 Android 14，实际最低要求为 23。修改后的两个版本分别在两台全新模拟器上从头验收。每次均新建不同笔记，保存后强制关闭，冷启动并逐字核对正文。",657)
y=block("自制工程样本   Archive Reader","原包可以安装、打开。点击 Open archived record 后，真实设备日志记录缺少 org.apache.http.ProtocolVersion。工作台确认目标系统提供相应共享库，添加声明并再测。独立测试人员通过网页点击导出包中的同一按钮，看到了 Read OK: HTTP/1.1。",y)
y=block("对照和失败样本","已声明所需共享库的对照包在 Android 14 和 15 均通过，网页下载文件与输入文件的 SHA-256 相同。把预期文本故意填错后，报告停止导出。在 Android 15 的测试实例上，My Tracks 因 32 位原生库与设备不匹配而安装失败，保留系统报错和限制说明。",y)
y=block("耗时与测量范围",f"按独立浏览器测试报告的第一条和最后一条事件计时，记事本任务约 {elapsed(oi)} 秒，归档读取样本约 {elapsed(ar)} 秒，均含专用模拟器冷启动。两条记录分别对应单次测试。独立离线审计另比对了 DEX 和资源，确认本轮修复的语义改动集中在清单。",y)
refs([("OI Notepad 项目","https://www.openintents.org/notepad/"),("F-Droid 历史样本","https://apt.izzysoft.de/fdroid/index/apk/org.openintents.notepad?repo=archive")])
end(y)
header(4,"落地与实施  /  重邮GGBond","试点路线与交付")
y=block("需求验证与差异","现有安卓模拟器能够运行应用，签名和打包工具能够生成 APK。维护人员仍须自行判断做何种修改以及如何验收。本作品把原始故障、最小修改和同任务复测关联起来。建议在内部测试终端换代时采集人工排查用时、修复后回归用时和复用次数，以同一任务衡量实际收益。",657)
y=block("资源与团队","目前由重庆邮电大学网络空间安全专业硕士研究生刘飞扬负责。其安卓开发、桌面工具和网络安全实践与样本分析及运行验证相关。原型使用一台约 16 GB 内存的 Windows 电脑，串行使用模拟器。当前规则模式无模型调用费；镜像存储、冷启动时间、测试人员复核构成主要成本。",y)
y=block("下一步","近期先在有授权的内部样本中扩充常见故障，尤其关注超出支持范围的误判。获得允许后，再对相同样本比较模型辅助与固定规则的试验轮数、人工介入和错误建议。中期积累真机与系统版本矩阵；三至五年扩展到组织内终端适配和历史版本回归，是否推广由试点测量决定。",y)
y=block("复核入口","启动源码包中的 start_workbench.ps1，打开本机工作台。优先复跑旧记事本和团队提供的归档读取器，再检查对照与失败分支。附件含修复样本、哈希、开源许可、独立测试记录、演示视频和可在本地克隆的代码仓库。",y)
refs([("F01 赛题说明","https://wangxinbei.taiji.com.cn/track/36"),("官方赛程","https://wangxinbei.taiji.com.cn/schedule")])
end(y)
c.save()
print(OUT)
