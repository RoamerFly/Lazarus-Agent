const el=id=>document.getElementById(id);
let config, job=null, sending=false, screenBusy=false, polling=false;
let askUi, runtimeUi, modificationUi;
const PROCESSING_NOTICE="正在处理，进度和结果会显示在对话中。";
function notice(s){el("notice").textContent=s;}
async function api(url,method="GET",body=null){
  const headers={};
  if(method==="POST") headers["x-reviver-token"]=config.token;
  if(typeof body==="string") headers["Content-Type"]="application/json";
  const res=await fetch(url,{method,headers,body});
  const out=await res.json();
  if(!res.ok) throw Error(typeof out.detail==="string"?out.detail:"工作台请求失败");
  return out;
}
function path(end){return "/api/jobs/"+job.id+end;}
async function ensureRuntime(targets,resume){
  if(!runtimeUi)return true;
  const ready=await runtimeUi.ensure(targets,resume);
  if(!ready)notice("所选版本正在等待本地运行环境；完成后请再次继续。");
  return ready;
}
const labels={"installation-floor":"原包目标级别低于系统安装门槛","native-abi-mismatch":"原生库与设备架构不匹配","requires-newer-platform":"应用需要较新的 Android 版本","native-textrel-blocks-target-upgrade":"原生库含文本重定位，直接提升目标 API 会影响加载"};
function render(){
  const busy=sending||job?.status==="running";
  document.querySelectorAll("[data-act]").forEach(b=>b.disabled=!job||busy||b.dataset.act==="candidate"&&!(job?.patch||job?.export?.modification_id)||b.dataset.act==="legacy"&&!config?.legacy);
  for(const id of ["file","demo","target"])el(id).disabled=busy||!config;
  for(const id of ["back","send","refresh"])el(id).disabled=!job||busy;
  askUi?.sync();
  modificationUi?.sync();
  renderChat(busy);
  renderScope(busy);
  renderMatrix(job);
  if(!job)return;
  const f=job.package;
  el("appname").textContent=f.label;
  const grid=el("facts");grid.replaceChildren();grid.className="";
  for(const [name,val] of [["应用名称",f.label],["包名",f.package],["版本",f.version],["系统级别",f.min_sdk+" 起，面向 API "+f.target_sdk],["原生库",f.native_abis.join(", ")||"未发现 lib/*.so"],["签名证书",f.certificate_sha256]]){
    const row=document.createElement("div");row.className="fact";
    for(const value of [name,val]){const v=document.createElement("span");v.textContent=value;row.append(v);}grid.append(row);
  }
  const issues=el("issues");issues.replaceChildren();
  for(const issue of job.assessment.static_findings){
    const v=document.createElement("div");v.className="warning";v.textContent=labels[issue.code]+" · "+issue.evidence;issues.append(v);
  }
  if(job.embedded_native_payload?.available){
    const v=document.createElement("div");v.className="warning";
    v.textContent="检测到经过结构核对的内置同架构 native payload；点击“修复并验证”会优先尝试该受限方案，仍须通过验证才会作为正式修复包交付。";
    issues.append(v);
  }
  if(!issues.children.length)issues.textContent="静态检查未发现已知阻断项，仍需运行验证。";
  const diag=(job.candidate_diagnosis||job.diagnosis)?.advice;
  const method=(diag?.source==="model"||diag?.source==="model-tools")?"模型 "+diag.model:diag?.model_status?.startsWith("fallback:")?"模型不可用，已回退到规则":"本地规则";
  el("advice").textContent=diag?method+" · "+diag.reason+(diag.evidence_ids?" ["+diag.evidence_ids.join(", ")+"]":""):"运行修复后显示本次诊断依据。";
  const trace=el("modelToolTrace");
  const names=Array.isArray(diag?.tool_trace)?diag.tool_trace.map(item=>typeof item==="string"?item:item?.name).filter(name=>typeof name==="string"&&/^[a-z_]{1,80}$/.test(name)):[];
  trace.hidden=!names.length;
  trace.textContent=names.length?"本轮模型读取的受限工具："+names.join("、"):"";
  const events=el("events"),offset=events.scrollTop,atEnd=events.scrollHeight-events.scrollTop-events.clientHeight<10;
  events.replaceChildren();
  job.events.forEach(x=>{const li=document.createElement("li");li.textContent=new Date(x.time).toLocaleTimeString()+"　"+x.text;events.append(li);});
  events.scrollTop=atEnd?events.scrollHeight:offset;
  el("badge").textContent=({ready:"就绪",running:"执行中",error:"需检查"})[job.status];
  el("report").hidden=false;el("report").href=path("/export/report");
  el("apk").hidden=!job.export;el("apk").href=path("/export/apk");el("verdict").textContent=job.export?.level||"";
  if(job.export && !matchesTestedScope(job)){
    el("apk").hidden=true;
    el("verdict").textContent="选择的范围已有变化。请重新验证；旧版本仍可在版本列表中下载。";
  }
  const env=matchesTestedScope(job)?job.export?.verified_environment:null;
  if(env){
    const arch=env.primary_cpu_abi?" · 应用使用 "+env.primary_cpu_abi:"";
    const platform=env.type==="redroid-arm-lab"?"离线 ARM 实验室":"模拟器";
    el("verdict").textContent+=" · "+platform+" API "+env.sdk+"（"+env.supported_abis.join(", ")+"）"+arch;
  }
  const native=job.native_candidate;
  el("nativeCandidate").hidden=!(native?.candidate&&["not-tested","failed"].includes(native.acceptance));
  el("nativeCandidate").textContent=native?.acceptance==="failed"?"下载试验候选 APK（未通过验证）":"下载试验候选 APK（未验收）";
  el("nativeCandidate").href=path("/export/native-candidate");
}
async function refreshScreen(){
  if(!job||screenBusy)return;screenBusy=true;
  const id=job.id;
  try{
    const r=await fetch(path("/screen"));
    if(!r.ok||job?.id!==id)return;
    const url=URL.createObjectURL(await r.blob()),img=el("screen");
    if(job?.id!==id){URL.revokeObjectURL(url);return;}
    const previous=img.src;img.src=url;if(previous.startsWith("blob:"))URL.revokeObjectURL(previous);
    img.hidden=false;el("standby").hidden=true;
  }finally{screenBusy=false;}
}
let lastFile=null,isDemo=false;
async function loadJob(file,demo){
  sending=true;render();notice("正在校验签名、读取清单…");
  try{
    const targets=selectedTargets();
    if(!targets.length)throw Error("请至少选择一个目标版本。");
    if(!config.editing_ready && !await ensureRuntime(targets,()=>loadJob(file,demo)))return;
    job=await api("/api/import/"+Math.max(...targets)+(demo?"?demo=true":""),"POST",file);
    el("message").value="";el("attachment").value="";el("attachmentName").textContent="";
    el("taskTap").value="";el("taskExpect").value="";el("acceptanceSteps").value="";lastFile=file;isDemo=demo;localStorage.setItem("reviverJob",job.id);el("screen").hidden=true;el("standby").hidden=false;notice("已导入 · Android "+(job.api-20));
  }catch(e){notice(e.message);if(job)el("target").value=job.api;}finally{sending=false;render();}
}
el("file").onchange=e=>{if(e.target.files[0])loadJob(e.target.files[0],false);};
el("demo").onclick=()=>loadJob(null,true);
el("target").onchange=async()=>{
  const requested=Number(el("target").value);
  if(!config.editing_ready && !await ensureRuntime(selectedTargets(),()=>{
    el("target").value=requested;
    el("target").dispatchEvent(new Event("change"));
  })){
    // The job still points at its old verified target.  Restore the selector
    // so the user can deliberately select the newly prepared target later.
    if(job && el("scope").value==="single")el("target").value=job.api;
    render();return;
  }
  if(!job || el("scope").value!=="single"){render();return;}
  sending=true;render();
  try{
    job=await api(path("/target/"+el("target").value),"POST");
    localStorage.setItem("reviverJob",job.id);
    el("screen").hidden=true;el("standby").hidden=false;
    notice("已沿用原包 · Android "+(job.api-20));
  }catch(e){notice(e.message);el("target").value=job.api;}
  finally{sending=false;render();}
};
document.querySelectorAll("[data-act]").forEach(b=>b.onclick=async()=>{
  sending=true;render();try{
    const tap=el("taskTap").value.trim(),expect=el("taskExpect").value.trim(),act=b.dataset.act;
    if(["repair","retry"].includes(act) && Boolean(tap)!==Boolean(expect))throw Error("请同时填写点击控件和预期内容。");
    let body=null;
    if(["repair","retry"].includes(act)){
      const targets=selectedTargets();
      if(!targets.length)throw Error("请至少选择一个目标版本。");
      if(!await ensureRuntime(targets,()=>b.click()))return;
      const highest=Math.max(...targets);
      if(job.api!==highest){
        if(act==="retry")throw Error("变更目标范围后请点击「修复并验证」，原任务仍保留在修复记录中。");
        job=await api(path("/target/"+highest),"POST");
        localStorage.setItem("reviverJob",job.id);
      }
      body={};if(tap)body.task={tap,expect};
      if(el("scope").value!=="single")body.targets=targets;
    }
    await api(path("/action/"+act),"POST",body?JSON.stringify(body):null);
    job=await api(path(""));notice("可在运行记录中查看进展。");
  }catch(e){notice(e.message);}finally{sending=false;render();}
});
async function sendInput(body){try{await api(path("/input"),"POST",JSON.stringify(body));await refreshScreen();}catch(e){notice(e.message);}}
el("screen").onclick=e=>{const rect=e.target.getBoundingClientRect(),img=e.target;
  sendInput({event:"tap",x:Math.floor((e.clientX-rect.left)*img.naturalWidth/rect.width),y:Math.floor((e.clientY-rect.top)*img.naturalHeight/rect.height)});
};
el("refresh").onclick=refreshScreen;el("back").onclick=()=>sendInput({event:"back"});
el("send").onclick=()=>sendInput({event:"text",value:el("text").value});
(async()=>{try{
  config=await api("/api/config");
  for(const a of Object.keys(config.targets).map(Number).sort((a,b)=>b-a)){const o=new Option("Android "+(a-20)+(config.targets[a]?"":" · 按需准备"),a);el("target").add(o);}
  el("demo").textContent="载入示例";el("demo").title=config.demo_label||"旧版记事本";
  if(config.targets[35])el("target").value=35;
  buildScopeControls();
  runtimeUi=LazarusRuntimePrepare.bind({api,notice});
  bindResources();
  askUi=LazarusAsk.bind({api,getJob:()=>job,notice});
  modificationUi=LazarusModifications.bind({api,getJob:()=>job,notice,selectedTargets,ensureRuntime,onStarted:async()=>{
    job=await api(path(""));notice("改造任务已开始，可在运行记录中查看进展。");
  }});
  // Range/multi-select edits only update the stated validation scope.  A
  // download begins on a concrete import, target switch, or repair action,
  // avoiding a premature image download while a checkbox combination is
  // still being chosen.
  el("scope").onchange=()=>render();
  const old=localStorage.getItem("reviverJob");if(old){try{job=await api("/api/jobs/"+old);restoreScope(job);}catch{localStorage.removeItem("reviverJob");}}
  render();
}catch(e){notice(e.message);}})();
setInterval(async()=>{if(!job||sending||polling)return;polling=true;const current=job;
  try{
    const next=await api(path(""));
    if(job===current&&!sending){
      job=next;
      if(job.status!=="running"&&el("notice").textContent===PROCESSING_NOTICE)notice("");
      render();
    }
  }catch(e){if(job===current&&!sending)notice(e.message);}finally{polling=false;}
},1400);
let lastPreview=0;
setInterval(()=>{
  if((!job?.tests.length&&!job?.modification&&!job?.feature_candidates&&job?.action!=="chat-feature") || document.hidden)return;
  const now=Date.now();
  if(job.status==="running" && job.tests.some(test=>test.environment==="redroid-arm-lab") && now-lastPreview<12000)return;
  lastPreview=now;
  refreshScreen().catch(()=>{});
},2300);
let chatSignature="", chatJobId=null, chatRows=[];
function renderChat(busy){
  const versions=job?.versions||[], messages=job?.messages||[];
  for(const id of ["message","sendMessage","attachment","problem"])el(id).disabled=!job||busy;
  el("works").disabled=!versions.length||busy;
  document.querySelector('[data-act="retry"]').disabled=!job?.pending_feedback||busy;
  if(!job)return;
  el("arch").textContent=job.package.native_abis.join(" · ")||"通用字节码";
  const signature=JSON.stringify([job.id,versions,messages,job.latest_feature_candidate]);
  if(signature===chatSignature)return;
  const same=el("feedbackVersion").dataset.job===job.id;
  const sameCount=Number(el("feedbackVersion").dataset.count)===versions.length;
  const selected=same&&sameCount?el("feedbackVersion").value:"";
  el("feedbackVersion").dataset.count=versions.length;
  chatSignature=signature;
  el("versions").replaceChildren();el("feedbackVersion").replaceChildren();
  el("feedbackVersion").dataset.job=job.id;
  if(job.latest_feature_candidate){
    el("feedbackVersion").add(new Option("最新候选",""));
    el("feedbackVersion").add(new Option("原始 APK","original"));
  }else if(!versions.length){
    el("feedbackVersion").add(new Option("原始 APK",""));
  }
  if(!versions.length)el("versions").textContent="通过验证后可在这里下载独立 APK。";
  for(const v of [...versions].reverse()){
    el("feedbackVersion").add(new Option("V"+v.number,v.number));
    const row=document.createElement("div"),name=document.createElement("strong"),badge=document.createElement("span"),link=document.createElement("a");
    row.className="version";name.textContent="修复版本 V"+v.number;
    const tested=v.compatibility?.targets || v.verified_targets || (v.environment?.sdk?[v.environment.sdk]:[]);
    if(tested.length)name.append(document.createTextNode(" · "+tested.map(androidName).join(" / ")));badge.className="pill";badge.textContent=v.phone;
    link.textContent="下载 V"+v.number;link.href=path("/versions/"+v.number);
    row.append(name,badge,link);el("versions").append(row);
  }
  if(selected && [...el("feedbackVersion").options].some(o=>o.value===selected))el("feedbackVersion").value=selected;
  const chat=el("messages");
  // Read scroll geometry before writes. Keep earlier bubbles stable while a user reads them.
  const sameJob=chatJobId===job.id, previousOffset=chat.scrollTop;
  const followEnd=!sameJob || chat.scrollHeight-chat.scrollTop-chat.clientHeight<24;
  const entries=messages.length?messages:[{role:"assistant",text:"已导入 "+job.package.label+"。点击「修复并验证」开始，或告诉我你遇到了什么问题。"}];
  const rows=entries.map(m=>JSON.stringify(m));
  let prefix=0;
  if(sameJob)while(prefix<rows.length && prefix<chatRows.length && rows[prefix]===chatRows[prefix])prefix++;
  while(chat.children.length>prefix)chat.lastElementChild.remove();
  const additions=document.createDocumentFragment();
  for(const m of entries.slice(prefix)){
    const bubble=document.createElement("div");bubble.className="bubble "+m.role;bubble.textContent=(m.kind&&m.version?"V"+m.version+" · ":"")+m.text;
    if(m.attachment){const image=document.createElement("img");image.alt="问题截图";image.src=path("/attachments/"+m.attachment);bubble.append(image);}
    if(m.localization_id||m.feature_candidate_id){
      const link=document.createElement("a");
      const endpoint=m.feature_candidate_id?"/feature-candidates/":"/localized/";
      const filePath=path(endpoint+encodeURIComponent(m.feature_candidate_id||m.localization_id));
      const acceptance=job.feature_candidates?.[m.feature_candidate_id]?.acceptance;
      link.textContent=acceptance?.status==="behavior-verified"?"下载已验收 APK":"下载候选 APK";
      link.href=filePath+(acceptance?.status==="behavior-verified"?"/verified":"");
      if(acceptance){
        const scope=document.createElement("div");
        scope.textContent=acceptance.rows.map(row=>{
          const c=row.candidate;
          if(!c)return androidName(row.api)+"：环境不可用";
          if(row.status==="behavior-verified")return androidName(row.api)+"：功能通过"+(row.comparison==="baseline-already-passed"?"（基线也通过）":"");
          if(row.status==="launch-verified")return androidName(row.api)+"：安装启动通过";
          if(c.launch==="passed")return androidName(row.api)+"：功能未通过";
          if(c.install==="passed")return androidName(row.api)+"：启动未通过";
          return androidName(row.api)+"：未通过安装校验";
        }).join(" · ");
        bubble.append(scope);
      }
      bubble.append(link);
      if(acceptance){
        const evidence=document.createElement("a");
        evidence.textContent="下载验收报告";evidence.href=filePath+"/acceptance";
        bubble.append(evidence);
      }
    }
    additions.append(bubble);
  }
  chat.append(additions);
  chatJobId=job.id;chatRows=rows;
  chat.scrollTop=followEnd?chat.scrollHeight:previousOffset;
}
function acceptanceTask(){
  const lines=el("acceptanceSteps").value.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
  if(lines.length){
    const actions={"点击":"tap","出现":"expect","未出现":"absent","输入":"input","替换输入":"replace_input","返回":"back","冷启动":"cold_start"};
    if(lines.length>16)throw Error("独立验收最多 16 步。");
    return {steps:lines.map((line,index)=>{
      const [label,...rest]=line.split(/[|｜]/);
      const action=Object.hasOwn(actions,label.trim())?actions[label.trim()]:null;
      const value=rest.join("|").trim();
      const needsValue=action!=="back"&&action!=="cold_start";
      if(!action||Boolean(value)!==needsValue)throw Error("第 "+(index+1)+" 步格式无效。");
      return needsValue?{action,value}:{action};
    })};
  }
  const tap=el("taskTap").value.trim(),expect=el("taskExpect").value.trim();
  if(Boolean(tap)!==Boolean(expect))throw Error("请完整填写复现步骤。");
  return tap?{tap,expect}:null;
}

async function feedback(kind){
  if(!job||sending||job.status==="running")return;
  const text=el("message").value.trim()||(kind==="works"?"我在真机上试了，这个版本可以运行。":"");
  if(!text){el("message").focus();notice("请输入问题或复测结果");return;}
  const num=el("feedbackVersion").value;
  if(kind==="works"&&(!num||num==="original")){notice("请选择已验证版本，或直接发送试用反馈。");return;}
  const body={kind,text,version:num&&num!=="original"?Number(num):null};
  if(num==="original")body.base_original=true;
  if(kind==="issue"){
    try{
      const task=acceptanceTask();
      if(task)body.task=task;
    }catch(error){notice(error.message);return;}
  }
  sending=true;render();
  try{
    const f=el("attachment").files[0];
    if(f){
      if(f.size>3*1024*1024)throw Error("截图不能超过 3 MiB");
      body.attachment=await new Promise((ok,fail)=>{const reader=new FileReader();reader.onload=()=>ok(reader.result);reader.onerror=fail;reader.readAsDataURL(f);});
    }
    body.use_configured_model=true;
    body.local_translate=true;
    if(kind==="issue"&&!body.attachment)body.targets=selectedTargets();
    job=await api(path("/message"),"POST",JSON.stringify(body));
    el("message").value="";el("attachment").value="";el("attachmentName").textContent="";
    notice(job.status==="running"?PROCESSING_NOTICE:kind==="issue"?"反馈已保存，可继续修复。":"真机复测结果已保存。");
  }catch(e){notice(e.message);}finally{sending=false;render();}
}
el("toModification").onclick=()=>{
  if(!job||sending||job.status==="running"){notice("请先导入 APK 并等待当前操作结束。");return;}
  modificationUi?.fromMessage(el("message").value.trim());
};
el("sendMessage").onclick=()=>feedback("issue");el("works").onclick=()=>feedback("works");
el("problem").onclick=()=>{LazarusDialogs.open("taskDialog");};
el("message").onkeydown=e=>{if(e.key==="Enter"&&e.ctrlKey)feedback("issue");};
el("attachment").onchange=()=>{el("attachmentName").textContent=el("attachment").files[0]?.name||"";};
el("home").onclick=()=>el("historyDialog").close();
el("closeHistory").onclick=()=>el("historyDialog").close();
el("history").onclick=async()=>{
  if(sending||job?.status==="running"){notice("请等待当前操作结束");return;}
  try{
    const jobs=await api("/api/jobs"),list=el("jobList");list.replaceChildren();list.classList.toggle("empty-state",jobs.length===0);
    for(const j of jobs){const b=document.createElement("button");b.textContent=j.label+" · Android "+(j.api-20)+" · "+new Date(j.updated||j.created).toLocaleString()+(j.phone?" · "+j.phone:"");
      b.onclick=async()=>{try{job=await api("/api/jobs/"+j.id);localStorage.setItem("reviverJob",job.id);restoreScope(job);
        el("screen").hidden=true;el("standby").hidden=false;el("message").value="";el("attachment").value="";el("attachmentName").textContent="";
        el("taskTap").value=job.task?.tap||"";el("taskExpect").value=job.task?.expect||"";el("acceptanceSteps").value="";el("historyDialog").close();render();
      }catch(e){notice(e.message);}};list.append(b);}
    if(!jobs.length)list.textContent="还没有修复记录";el("historyDialog").showModal();
  }catch(e){notice(e.message);}
};
