const androidName = apiLevel => ({34:"Android 14",35:"Android 15",36:"Android 16"})[apiLevel] || "API "+apiLevel;

function selectedTargets(){
  const levels = Object.keys(config?.targets || {}).map(Number).sort((a,b)=>a-b);
  const upper = Number(el("target").value);
  if(el("scope").value==="upto")return levels.filter(n=>n<=upper);
  if(el("scope").value==="multiple"){
    return [...document.querySelectorAll("#versionsToTest input:checked")].map(input=>Number(input.value)).sort((a,b)=>a-b);
  }
  return upper ? [upper] : [];
}

function matchesTestedScope(state){
  const tested=state.compatibility?.targets || [state.api];
  const selected=selectedTargets();
  return tested.length===selected.length && selected.every(level=>tested.includes(level));
}

function buildScopeControls(){
  for(const level of Object.keys(config.targets).map(Number).sort((a,b)=>a-b)){
    const label=document.createElement("label"), check=document.createElement("input");
    check.type="checkbox";check.value=level;check.checked=level===Number(el("target").value);
    check.onchange=()=>render();
    label.append(check,androidName(level)+(config.targets[level]?"":"（缺少环境）"));
    el("versionsToTest").append(label);
  }
}

function renderScope(busy){
  const mode=el("scope").value, levels=selectedTargets();
  el("versionsToTest").hidden=mode!=="multiple";
  el("target").hidden=mode==="multiple";
  el("targetLabel").hidden=mode==="multiple";
  el("targetLabel").textContent=mode==="upto"?"验证上限":"目标系统";
  el("scope").disabled=busy;
  for(const check of document.querySelectorAll("#versionsToTest input"))check.disabled=busy;
  el("scopeSummary").textContent=levels.length ? (levels.length===1?androidName(levels[0]):levels.length+" 个版本")+" · 验证范围" : "选择验证范围";
  const hint=el("scopeHint");
  if(mode==="single")hint.textContent="";
  else if(!levels.length)hint.textContent="请至少选择一个 Android 版本。";
  else {
    const missing=levels.filter(n=>!config.targets[n]).map(androidName);
    hint.textContent="请求验证 "+levels.map(androidName).join("、")+"。使用同一 APK 逐项验证；Android 13 及更低版本不在当前测试范围。"+(missing.length?" 缺少运行环境："+missing.join("、"):"");
  }
}

function restoreScope(state){
  const targets=state.compatibility?.targets || [state.api];
  el("scope").value=targets.length>1?"multiple":"single";
  el("target").value=state.api;
  for(const check of document.querySelectorAll("#versionsToTest input"))check.checked=targets.includes(Number(check.value));
}

function renderMatrix(state){
  const matrix=state?.compatibility, section=el("matrixResults");
  section.hidden=!matrix;
  if(!matrix)return;
  const rows=el("matrixRows");rows.replaceChildren();
  const summary=document.createElement("p");summary.className="scopehint";
  const titles={passed:"所选系统均已通过",failed:"部分或全部目标未通过",running:"正在逐版本验证",pending:"等待构建与验证"};
  summary.textContent=(titles[matrix.status]||"逐项验证结果")+" · "+matrix.targets.map(androidName).join(" / ");
  rows.append(summary);
  for(const row of matrix.rows || []){
    const entry=document.createElement("div");
    const names={pending:"等待验证",running:"验证中",passed:"通过",failed:"未通过",unavailable:"缺少运行环境",skipped:"未验证"};
    entry.className="matrixRow "+row.status;
    const detail=typeof row.failure==="string"?row.failure:row.failure?.message;
    const evidence=row.installed_sha256?" · 已安装 SHA-256 "+row.installed_sha256.slice(0,12)+"…":"";
    entry.textContent=androidName(row.api)+" · "+(names[row.status]||row.status)+(row.verification_level?" · "+row.verification_level:"")+(detail?" · "+detail:"")+evidence;
    if(row.installed_sha256)entry.title=row.installed_sha256;
    rows.append(entry);
  }
}
