/* Explicit-consent, on-demand runtime preparation UI. */
(()=>{
  const $=id=>document.getElementById(id);
  function bind({api,notice}){
    const dialog=$("runtimeDialog"), text=$("runtimePrepareText"), progress=$("runtimePrepareProgress");
    const accept=$("runtimeTerms"), begin=$("runtimePrepareStart"), cancel=$("runtimePrepareCancel"), close=$("runtimePrepareClose");
    let status=null, pending=[], afterReady=null;
    const title=apiLevel=>"Android "+({34:"14",35:"15",36:"16"}[apiLevel]||apiLevel);
    const readyFor=targets=>targets.every(target=>status?.targets?.[target]?.ready);
    function draw(){
      const active=status?.active, targetText=pending.map(title).join("、")||"所选版本";
      if(active?.status==="running"){
        text.textContent="正在后台串行准备 "+active.targets.map(title).join("、")+"。准备期间不能启动修复或切换工具链。";
        begin.disabled=true; cancel.hidden=false;
      }else if(status?.terms_accepted){
        text.textContent=targetText+" 缺少本地验证环境，将复用已完整组件；仅缺少的官方组件才会联网下载。此前已确认下载条款。";
        begin.disabled=false; cancel.hidden=true;
      }else{
        text.textContent=targetText+" 需要 Android SDK、模拟器镜像和 Java 运行时。完整组件会直接复用，不会联网。若有缺失，下载前必须由你明确同意发布方条款。";
        begin.disabled=!accept.checked; cancel.hidden=true;
      }
      progress.replaceChildren();
      for(const event of (active?.events||[]).slice(-6)){
        const item=document.createElement("div"), current=Number(event.current), total=Number(event.total);
        item.textContent=(event.api?title(event.api)+" · ":"")+(event.component||"运行环境")+" · "+(event.message||"")+(total?" · "+Math.min(100,Math.round(current/total*100))+"%":"");
        progress.append(item);
      }
      if(active && active.status!=="running" && active.message){const item=document.createElement("div");item.textContent=active.message;progress.append(item);}
    }
    async function refresh(){status=await api("/api/runtime");draw();return status;}
    let timer=null;
    function completeIfReady(){
      if(status?.active?.status!=="ready" || !readyFor(pending))return;
      const resume=afterReady; afterReady=null; dialog.close();
      if(resume)queueMicrotask(resume);
    }
    async function settle(){
      // A user may request a wider scope while a previous one-target
      // preparation is still active. Once it finishes, prepare only the
      // remaining selected target(s), then resume the original action.
      if(status?.active?.status==="ready" && !readyFor(pending)){
        await beginPrepare(); return;
      }
      completeIfReady();
    }
    function poll(){clearInterval(timer);timer=setInterval(async()=>{try{await refresh();if(status?.active?.status!=="running"){clearInterval(timer);await settle();}}catch(error){notice(error.message);clearInterval(timer);}},900);}
    async function beginPrepare(){
      try{
        const result=await api("/api/runtime/prepare","POST",JSON.stringify({targets:pending,accept_terms:accept.checked||status?.terms_accepted}));
        await refresh();
        if(result.ready){dialog.close();notice("所选运行环境已完整可用，没有联网下载。");completeIfReady();return;}
        poll();notice("已开始后台准备；完成后可继续导入或修复。");
      }catch(error){notice(error.message);}
    }
    begin.addEventListener("click",beginPrepare);accept.addEventListener("change",draw);
    cancel.addEventListener("click",async()=>{try{afterReady=null;const result=await api("/api/runtime/cancel","POST");notice(result.message);await refresh();}catch(error){notice(error.message);}});
    close.addEventListener("click",()=>dialog.close());
    return {async ensure(targets,resume){
      pending=[...new Set(targets)].sort((a,b)=>a-b); afterReady=typeof resume==="function"?resume:null;
      await refresh();
      if(readyFor(pending))return true;
      if(status?.terms_accepted)await beginPrepare();else{accept.checked=false;dialog.showModal();draw();}
      return false;
    },refresh};
  }
  window.LazarusRuntimePrepare={bind};
})();
