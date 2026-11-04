/* User-requested APK modifications, deliberately separate from compatibility repair. */
(()=>{
  const byId=id=>document.getElementById(id);
  const text=value=>typeof value==="string"?value:"";
  function bind({api,getJob,notice,selectedTargets,ensureRuntime,onStarted}){
    const kind=byId("modificationKind"), request=byId("modificationRequest"), base=byId("modificationBaseVersion");
    const planButton=byId("modificationPlan"), runButton=byId("modificationRun"), reviseButton=byId("modificationRevise"), status=byId("modificationStatus");
    const remember=byId("rememberCriteria"), localize=byId("localizeCriteria"), rows=byId("localizeRows"), useModel=byId("modificationUseModel");
    const browseButton=byId("browseLocalizationResources"), resourceList=byId("localizeResourceList");
    let plan=null, jobId=null, busy=false, dirty=false, planSignature="";
    const setStatus=value=>{status.textContent=value;};
    const selectedKind=()=>kind.value;
    function previewKind(){
      if(selectedKind()!=="auto")return selectedKind();
      return /(中文|汉化|翻译|语言|locale|locali[sz]e|translation)/.test(request.value.toLowerCase())?"localize-zh":"remember-file";
    }
    function addLocalizationRow(value={}){
      const row=document.createElement("div");row.className="localizationRow";
      const resource=document.createElement("input");resource.placeholder="资源名，可留空";resource.maxLength=120;resource.value=text(value.resource);
      const original=document.createElement("input");original.placeholder="原文";original.maxLength=200;original.value=text(value.original);
      const translated=document.createElement("input");translated.placeholder="简体中文";translated.maxLength=200;translated.value=text(value.translated);
      const verify=document.createElement("label");verify.className="localizationVerify";const checkbox=document.createElement("input");checkbox.type="checkbox";checkbox.checked=value.verify!==false;checkbox.setAttribute("aria-label","作为界面验收项");verify.append(checkbox,document.createTextNode("验收"));
      const remove=document.createElement("button");remove.type="button";remove.textContent="移除";
      for(const input of [resource,original,translated])input.addEventListener("input",invalidate);
      checkbox.addEventListener("change",invalidate);
      remove.addEventListener("click",()=>{if(rows.children.length>1){row.remove();invalidate();}});
      row.append(resource,original,translated,verify,remove);rows.append(row);
    }
    function readTranslations({strict=true}={}){
      const translations=Object.create(null), resources=Object.create(null), checks=[];
      for(const row of rows.querySelectorAll(".localizationRow")){
        const [resource,original,translated,verify]=row.querySelectorAll("input");
        const source=original.value.trim(), target=translated.value.trim(), resourceName=resource.value.trim();
        if(!source&&!target&&!resourceName)continue;
        if(!source||!target){if(strict)throw Error("每条译文都需要原文和简体中文。");continue;}
        if(Object.hasOwn(translations,source)){if(strict)throw Error("原文不能重复："+source);continue;}
        translations[source]=target;if(verify.checked)checks.push({original:source,translated:target});
        if(resourceName)resources[source]=resourceName;
      }
      if(strict&&!Object.keys(translations).length)throw Error("请至少填写一条译文。");
      if(strict&&!checks.length)throw Error("请至少勾选一条译文作为界面验收项。");
      const tap=byId("localeTap").value.trim(),expect=byId("localeExpect").value.trim();
      if(strict&&Boolean(tap)!==Boolean(expect))throw Error("请同时填写操作控件和预期结果。");
      return {translations,resources,checks,...(tap||expect?{operation:{tap,expect}}:{})};
    }
    function criteria({strict=true}={}){
      if(previewKind()==="localize-zh")return readTranslations({strict});
      const pairs=[["rememberFirstTap","rememberFirstExpect"],["rememberSecondTap","rememberSecondExpect"]];
      const selects=pairs.map(([tap,expect])=>({tap:byId(tap).value.trim(),expect:byId(expect).value.trim()}));
      if(strict&&selects.some(step=>!step.tap||!step.expect))throw Error("请填写 A、B 两个记录的控件和预期内容。");
      return {selects};
    }
    function signature(){return JSON.stringify({kind:selectedKind(),request:request.value.trim(),criteria:criteria({strict:false}),base:base.value,targets:selectedTargets(),use_model:useModel.checked});}
    function invalidate(){
      if(plan){plan=null;planSignature="";setStatus("输入或验证范围已变化，请重新生成改造计划。");}
      dirty=true;runButton.disabled=true;renderCriteria();
    }
    function renderCriteria(){const localized=previewKind()==="localize-zh";remember.hidden=localized;localize.hidden=!localized;useModel.disabled=selectedKind()!=="auto";}
    function requestBody(){
      const description=request.value.trim();if(!description)throw Error("请描述想要改成什么。");
      const targets=selectedTargets();if(!targets.length)throw Error("请至少选择一个目标 Android 版本。");
      const body={request:description,kind:selectedKind(),criteria:criteria(),targets,use_model:selectedKind()==="auto"&&useModel.checked};
      if(base.value)body.base_version=Number(base.value);return body;
    }
    function renderRunSteps(current){
      const runs=Array.isArray(current?.runs)?current.runs:[];
      const entries=[];
      for(const run of runs.slice(-3)){
        entries.push(["改造尝试",text(run.status),text(run.failure)].filter(Boolean).join(" · "));
        for(const [label,proof] of [["基准复现",run.baseline],["回编对照",run.control]]){
          if(proof)entries.push([label,text(proof.result),text(proof.message)].filter(Boolean).join(" · "));
        }
        if(run.build){
          const change=run.build.transform||{};
          entries.push("候选 APK · SHA-256 "+text(run.build.sha256));
          const files=change.changed_files||(change.changed_file?[change.changed_file]:[]);
          for(const file of files)entries.push("修改 "+file+(change.target_method?" · "+change.target_method:""));
          if(Number.isInteger(change.edits))entries.push("写入 "+change.edits+" 条简体中文资源");
        }
        for(const row of run.compatibility?.rows||[]){
          const result=row.functional;
          entries.push(["Android "+(row.api-20),result?.message||row.failure||row.status].filter(Boolean).join(" · "));
        }
      }
      for(const run of runs.slice(-8)){
        const label=[text(run.step)||text(run.kind)||"验证",text(run.message)].filter(Boolean).join(" · ");
        if(label&&!(label==="验证"))entries.push(label);
      }
      return entries.length?"\n验证步骤：\n"+entries.join("\n"):"";
    }
    function describe(current){
      if(!current)return "";
      const values=[text(current.description),text(current.message)];
      if(current.source_sha256)values.push((current.base_version?"基于 V"+current.base_version:"基于导入原包")+" · SHA-256 "+current.source_sha256.slice(0,12));
      if(Array.isArray(current.targets))values.push("目标系统 "+current.targets.map(api=>"Android "+(api-20)).join(" / "));
      values.push(current.tool_source?"计划工具："+text(current.tool_source):"");
      if(Array.isArray(current.limitations))values.push(...current.limitations.filter(item=>typeof item==="string"));
      return values.filter(Boolean).join("\n")+renderRunSteps(current);
    }
    function updateBaseOptions(current){
      const selected=base.value;base.replaceChildren(new Option("导入的原始 APK", ""));
      for(const version of [...(current?.versions||[])].reverse())base.add(new Option("已归档 V"+version.number+" · "+text(version.level),String(version.number)));
      if([...base.options].some(option=>option.value===selected))base.value=selected;
    }
    function restorePlan(livePlan){
      if(!livePlan)return;
      if(typeof livePlan.request==="string")request.value=livePlan.request;
      if(["auto","remember-file","localize-zh"].includes(livePlan.kind))kind.value=livePlan.kind;
      if(livePlan.base_version!==undefined&&[...base.options].some(option=>option.value===String(livePlan.base_version)))base.value=String(livePlan.base_version);
      useModel.checked=Boolean(livePlan.use_model);
      const input=livePlan.criteria||{};
      byId("localeTap").value=text(input.operation?.tap);byId("localeExpect").value=text(input.operation?.expect);
      if(Array.isArray(input.selects)&&input.selects.length>=2){
        byId("rememberFirstTap").value=text(input.selects[0].tap);byId("rememberFirstExpect").value=text(input.selects[0].expect);
        byId("rememberSecondTap").value=text(input.selects[1].tap);byId("rememberSecondExpect").value=text(input.selects[1].expect);
      }
      if(input.translations&&typeof input.translations==="object"){
        const checked=new Set((input.checks||[]).map(item=>item?.original));
        rows.replaceChildren();for(const [original,translated] of Object.entries(input.translations))addLocalizationRow({original,translated,resource:input.resources?.[original],verify:checked.has(original)});
      }
      if(!rows.children.length)addLocalizationRow();renderCriteria();
    }
    function sync(){
      const current=getJob(), changed=current?.id!==jobId;
      if(changed){
        request.value="";kind.value="auto";base.value="";useModel.checked=false;
        for(const id of ["rememberFirstTap","rememberFirstExpect","rememberSecondTap","rememberSecondExpect","localeTap","localeExpect"])byId(id).value="";
        rows.replaceChildren();addLocalizationRow();resourceList.replaceChildren();resourceList.hidden=true;
        jobId=current?.id||null;plan=null;planSignature="";dirty=false;updateBaseOptions(current);restorePlan(current?.modification?.plan);setStatus(describe(current?.modification));}
      else updateBaseOptions(current);
      const modification=current?.modification, livePlan=modification?.plan;
      if(plan&&planSignature!==signature())invalidate();
      if(!dirty&&!plan&&livePlan?.id){plan=livePlan;restorePlan(livePlan);planSignature=signature();}
      if(!busy&&modification?.status==="running")setStatus("正在按改造计划构建并验证…"+renderRunSteps(modification));
      else if(!busy&&!dirty&&modification?.message)setStatus(describe(modification));
      planButton.disabled=!current||busy||current.status==="running";
      browseButton.disabled=!current||busy||current.status==="running";
      runButton.disabled=!(current&&plan?.id&&!dirty&&!busy&&current.status!=="running"&&modification?.status!=="passed");
      const feedback=current?.pending_feedback;
      reviseButton.hidden=!(feedback&&feedback.version&&modification?.plan);
      reviseButton.disabled=busy||current?.status==="running";
      renderCriteria();
    }
    async function makePlan(){
      const current=getJob();if(!current||busy)return;let payload;
      try{payload=requestBody();}catch(error){setStatus(error.message);notice(error.message);return;}
      const submitted=signature();busy=true;sync();setStatus("正在保存基准 APK 和验收条件…");
      try{
        const result=await api("/api/jobs/"+encodeURIComponent(current.id)+"/modify/plan","POST",JSON.stringify(payload));
        if(getJob()?.id!==current.id)return;
        if(signature()!==submitted){invalidate();setStatus("保存期间输入已变化，请按当前内容重新生成计划。");return;}
        plan=result.plan||result;if(!plan?.id)throw Error("工作台没有返回可执行的改造计划。");
        current.modification={plan,status:"planned",runs:current.modification?.runs||[],message:describe(plan)};
        dirty=false;planSignature=signature();setStatus(describe(plan)||"计划已生成。确认后可执行并验证。");notice("改造计划已生成，请核对后执行。");
      }catch(error){setStatus(error.message||"生成改造计划失败。");notice(error.message||"生成改造计划失败。");}
      finally{busy=false;sync();}
    }
    function addResourceToRows(item){
      const source=text(item.original), translated=text(item.translated), name=text(item.name);
      if(!source)return;
      for(const row of rows.querySelectorAll(".localizationRow")){
        const [, original]=row.querySelectorAll("input");
        if(original.value.trim()===source)return;
      }
      addLocalizationRow({resource:name,original:source,translated,verify:true});
      invalidate();
    }
    function showResources(strings){
      resourceList.replaceChildren();
      if(!strings.length){resourceList.hidden=false;resourceList.textContent="当前基准 APK 没有返回可供汉化的字符串资源。";return;}
      for(const item of strings){
        if(!item||typeof item!=="object"||!text(item.original))continue;
        const row=document.createElement("div"), info=document.createElement("span"), add=document.createElement("button");
        row.className="localize-resource";
        info.textContent=[text(item.name),text(item.original),text(item.translated)].filter(Boolean).join(" · ");
        add.type="button";add.textContent="加入译文";add.addEventListener("click",()=>addResourceToRows(item));
        row.append(info,add);resourceList.append(row);
      }
      resourceList.hidden=false;
      if(!resourceList.children.length)resourceList.textContent="接口未返回可用的字符串条目。";
    }
    async function browseResources(){
      const current=getJob();if(!current||busy)return;
      busy=true;sync();setStatus("正在扫描当前基准 APK 的字符串资源…");
      try{
        const body={base_version:base.value?Number(base.value):null};
        const result=await api("/api/jobs/"+encodeURIComponent(current.id)+"/modify/resources","POST",JSON.stringify(body));
        if(getJob()?.id!==current.id)return;
        showResources(Array.isArray(result.strings)?result.strings:[]);
        setStatus("已扫描到 "+(Array.isArray(result.strings)?result.strings.length:0)+" 条可供选择的字符串资源。");
      }catch(error){setStatus(error.message||"扫描字符串资源失败。");notice(error.message||"扫描字符串资源失败。");}
      finally{busy=false;sync();}
    }
    async function executePlan(){
      const current=getJob();if(!current||!plan?.id||dirty||busy)return;const targets=selectedTargets();
      if(!targets.length){notice("请至少选择一个目标 Android 版本。");return;}if(!await ensureRuntime(targets,executePlan))return;
      busy=true;sync();setStatus("正在生成候选 APK 并逐项验证…");
      try{await api("/api/jobs/"+encodeURIComponent(current.id)+"/modify/run","POST",JSON.stringify({plan_id:plan.id}));await onStarted?.();}
      catch(error){setStatus(error.message||"执行改造计划失败。");notice(error.message||"执行改造计划失败。");}
      finally{busy=false;sync();}
    }
    function reviseFromFeedback(){
      const current=getJob(), feedback=current?.pending_feedback;
      if(!current||!feedback?.version||!current.modification?.plan)return;
      const matchingRun=[...(current.modification.runs||[])].reverse().find(run=>run.status==="passed"&&run.version===feedback.version);
      restorePlan(matchingRun?.plan||current.modification.plan);
      base.value=String(feedback.version);
      request.value=(matchingRun?.plan?.request||current.modification.plan.request)+"。V"+feedback.version+" 真机反馈："+text(feedback.text);
      plan=null;planSignature="";dirty=true;
      setStatus("已基于 V"+feedback.version+" 回填验收条件和反馈。请先重新生成改造计划。");
      byId("modificationDetails").open=true;sync();
    }
    kind.addEventListener("change",invalidate);request.addEventListener("input",invalidate);base.addEventListener("change",invalidate);useModel.addEventListener("change",invalidate);
    for(const id of ["rememberFirstTap","rememberFirstExpect","rememberSecondTap","rememberSecondExpect","localeTap","localeExpect"])byId(id).addEventListener("input",invalidate);
    byId("addLocalizationRow").addEventListener("click",()=>{addLocalizationRow();invalidate();});browseButton.addEventListener("click",browseResources);planButton.addEventListener("click",makePlan);runButton.addEventListener("click",executePlan);reviseButton.addEventListener("click",reviseFromFeedback);
    function fromMessage(value){
      kind.value="auto";request.value=value;invalidate();byId("modificationDetails").open=true;
      request.focus();notice("请补全改造的验收条件，再生成计划。");
    }
    addLocalizationRow();renderCriteria();return {sync,fromMessage};
  }
  window.LazarusModifications={bind};
})();
