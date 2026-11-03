const el=id=>document.getElementById(id);
let config, job=null, sending=false, screenBusy=false, polling=false;
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
const labels={"installation-floor":"原包目标级别低于系统安装门槛","native-abi-mismatch":"原生库与设备架构不匹配","requires-newer-platform":"应用需要较新的 Android 版本","native-textrel-blocks-target-upgrade":"原生库含文本重定位，直接提升目标 API 会影响加载"};
function render(){
  const busy=sending||job?.status==="running";
  document.querySelectorAll("[data-act]").forEach(b=>b.disabled=!job||busy||b.dataset.act==="candidate"&&!job?.patch||b.dataset.act==="legacy"&&!config?.legacy);
  for(const id of ["file","demo","target"])el(id).disabled=busy||!config;
  for(const id of ["back","send","refresh"])el(id).disabled=!job||busy;
  renderChat(busy);
  if(!job)return;
  const f=job.package;
  el("appname").textContent=f.label;
  const grid=el("facts");grid.replaceChildren();grid.className="";
  for(const [name,val] of [["包名",f.package],["版本",f.version],["系统级别",f.min_sdk+" 起，面向 API "+f.target_sdk],["原生库",f.native_abis.join(", ")||"未发现 lib/*.so"],["签名证书",f.certificate_sha256]]){
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
  const method=diag?.source==="model"?"模型 "+diag.model:diag?.model_status?.startsWith("fallback:")?"模型不可用，已回退到规则":"本地规则";
  el("advice").textContent=diag?method+" · "+diag.reason+(diag.evidence_ids?" ["+diag.evidence_ids.join(", ")+"]":""):"运行修复后显示本次诊断依据。";
  const events=el("events"),offset=events.scrollTop,atEnd=events.scrollHeight-events.scrollTop-events.clientHeight<10;
  events.replaceChildren();
  job.events.forEach(x=>{const li=document.createElement("li");li.textContent=new Date(x.time).toLocaleTimeString()+"　"+x.text;events.append(li);});
  events.scrollTop=atEnd?events.scrollHeight:offset;
  el("badge").textContent=({ready:"就绪",running:"执行中",error:"需检查"})[job.status];
  el("report").hidden=false;el("report").href=path("/export/report");
  el("apk").hidden=!job.export;el("apk").href=path("/export/apk");el("verdict").textContent=job.export?.level||"";
  const env=job.export?.verified_environment;
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
    job=await api("/api/import/"+el("target").value+(demo?"?demo=true":""),"POST",file);
    el("message").value="";el("attachment").value="";el("attachmentName").textContent="";
    el("taskTap").value="";el("taskExpect").value="";lastFile=file;isDemo=demo;localStorage.setItem("reviverJob",job.id);el("screen").hidden=true;el("standby").hidden=false;notice("已导入 · Android "+(job.api-20));
  }catch(e){notice(e.message);if(job)el("target").value=job.api;}finally{sending=false;render();}
}
el("file").onchange=e=>{if(e.target.files[0])loadJob(e.target.files[0],false);};
el("demo").onclick=()=>loadJob(null,true);
el("target").onchange=async()=>{
  if(!job)return;
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
    await api(path("/action/"+act),"POST",["repair","retry"].includes(act)&&tap?JSON.stringify({task:{tap,expect}}):null);
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
  config=await api("/api/config");el("engine").textContent=config.model?"模型已配置":"本地规则可用";
  for(const a of Object.keys(config.targets).map(Number).sort((a,b)=>b-a)){const o=new Option("Android "+(a-20)+(config.targets[a]?"":" · 缺少镜像"),a);o.disabled=!config.targets[a];el("target").add(o);}
  el("demo").textContent="示例 · "+(config.demo_label||"旧版记事本");
  if(config.targets[35])el("target").value=35;
  const old=localStorage.getItem("reviverJob");if(old){try{job=await api("/api/jobs/"+old);el("target").value=job.api;}catch{localStorage.removeItem("reviverJob");}}
  render();
}catch(e){notice(e.message);}})();
setInterval(async()=>{if(!job||sending||polling)return;polling=true;const id=job.id;
  try{const next=await api(path(""));if(job?.id===id){job=next;render();}}catch(e){notice(e.message);}finally{polling=false;}
},1400);
let lastPreview=0;
setInterval(()=>{
  if(!job?.tests.length || document.hidden)return;
  const now=Date.now();
  if(job.status==="running" && job.tests.some(test=>test.environment==="redroid-arm-lab") && now-lastPreview<12000)return;
  lastPreview=now;
  refreshScreen().catch(()=>{});
},2300);
let chatSignature="";
function renderChat(busy){
  const versions=job?.versions||[], messages=job?.messages||[];
  for(const id of ["message","sendMessage","attachment","problem"])el(id).disabled=!job||busy;
  el("works").disabled=!versions.length||busy;
  document.querySelector('[data-act="retry"]').disabled=!job?.pending_feedback||busy;
  if(!job)return;
  el("arch").textContent=job.package.native_abis.join(" · ")||"通用字节码";
  const signature=JSON.stringify([job.id,versions,messages]);
  if(signature===chatSignature)return;
  const same=el("feedbackVersion").dataset.job===job.id;
  const sameCount=Number(el("feedbackVersion").dataset.count)===versions.length;
  const selected=same&&sameCount?el("feedbackVersion").value:"";
  el("feedbackVersion").dataset.count=versions.length;
  chatSignature=signature;
  el("versions").replaceChildren();el("feedbackVersion").replaceChildren();
  el("feedbackVersion").dataset.job=job.id;
  if(!versions.length){el("versions").textContent="通过验证后可在这里下载独立 APK。";el("feedbackVersion").add(new Option("当前任务",""));}
  for(const v of [...versions].reverse()){
    el("feedbackVersion").add(new Option("V"+v.number,v.number));
    const row=document.createElement("div"),name=document.createElement("strong"),badge=document.createElement("span"),link=document.createElement("a");
    row.className="version";name.textContent="修复版本 V"+v.number;badge.className="pill";badge.textContent=v.phone;
    link.textContent="下载 V"+v.number;link.href=path("/versions/"+v.number);
    row.append(name,badge,link);el("versions").append(row);
  }
  if(selected && [...el("feedbackVersion").options].some(o=>o.value===selected))el("feedbackVersion").value=selected;
  const chat=el("messages");chat.replaceChildren();
  const entries=messages.length?messages:[{role:"assistant",text:"已导入 "+job.package.label+"。点击「修复并验证」开始，或告诉我你遇到了什么问题。"}];
  for(const m of entries){
    const bubble=document.createElement("div");bubble.className="bubble "+m.role;bubble.textContent=(m.kind&&m.version?"V"+m.version+" · ":"")+m.text;
    if(m.attachment){const image=document.createElement("img");image.alt="问题截图";image.src=path("/attachments/"+m.attachment);bubble.append(image);}
    chat.append(bubble);
  }
  chat.scrollTop=chat.scrollHeight;
}
async function feedback(kind){
  if(!job||sending||job.status==="running")return;
  const text=el("message").value.trim()||(kind==="works"?"我在真机上试了，这个版本可以运行。":"");
  if(!text){el("message").focus();notice("请输入问题或复测结果");return;}
  const num=el("feedbackVersion").value;
  const body={kind,text,version:num?Number(num):null};
  const tap=el("taskTap").value.trim(),expect=el("taskExpect").value.trim();
  if(kind==="issue" && (tap||expect)){if(!tap||!expect){notice("请完整填写复现步骤");return;}body.task={tap,expect};}
  sending=true;render();
  try{
    const f=el("attachment").files[0];
    if(f){
      if(f.size>3*1024*1024)throw Error("截图不能超过 3 MiB");
      body.attachment=await new Promise((ok,fail)=>{const reader=new FileReader();reader.onload=()=>ok(reader.result);reader.onerror=fail;reader.readAsDataURL(f);});
    }
    job=await api(path("/feedback"),"POST",JSON.stringify(body));
    el("message").value="";el("attachment").value="";el("attachmentName").textContent="";
    notice(kind==="issue"?"反馈已保存，可继续修复。":"真机复测结果已保存。");
  }catch(e){notice(e.message);}finally{sending=false;render();}
}
el("sendMessage").onclick=()=>feedback("issue");el("works").onclick=()=>feedback("works");
el("problem").onclick=()=>{el("taskDetails").open=true;el("message").focus();};
el("message").onkeydown=e=>{if(e.key==="Enter"&&e.ctrlKey)feedback("issue");};
el("attachment").onchange=()=>{el("attachmentName").textContent=el("attachment").files[0]?.name||"";};
el("home").onclick=()=>el("historyDialog").close();
el("closeHistory").onclick=()=>el("historyDialog").close();
el("history").onclick=async()=>{
  if(sending||job?.status==="running"){notice("请等待当前操作结束");return;}
  try{
    const jobs=await api("/api/jobs"),list=el("jobList");list.replaceChildren();
    for(const j of jobs){const b=document.createElement("button");b.textContent=j.label+" · Android "+(j.api-20)+" · "+new Date(j.updated||j.created).toLocaleString()+(j.phone?" · "+j.phone:"");
      b.onclick=async()=>{try{job=await api("/api/jobs/"+j.id);localStorage.setItem("reviverJob",job.id);el("target").value=job.api;
        el("screen").hidden=true;el("standby").hidden=false;el("message").value="";el("attachment").value="";el("attachmentName").textContent="";
        el("taskTap").value=job.task?.tap||"";el("taskExpect").value=job.task?.expect||"";el("historyDialog").close();render();
      }catch(e){notice(e.message);}};list.append(b);}
    if(!jobs.length)list.textContent="还没有修复记录";el("historyDialog").showModal();
  }catch(e){notice(e.message);}
};
