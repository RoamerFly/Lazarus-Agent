/* Bounded assistant UI.  Host supplies authenticated API and the current job. */
(()=>{
  const byId=id=>document.getElementById(id);
  function bind({api,getJob,notice}){
    const input=byId("askQuestion"), optIn=byId("askUseModel"), send=byId("askSend"), out=byId("askAnswer");
    if(!input||!optIn||!send||!out) throw Error("问助手所需控件缺失");
    let lastJobId=getJob()?.id||null;
    const show=(text,source)=>{out.hidden=false;out.textContent=(source==="model"?"已使用已配置模型（仅解释）\n":"本地说明\n")+text;};
    const update=()=>{send.disabled=!input.value.trim();};
    const sync=()=>{
      const current=getJob()?.id||null;
      if(current!==lastJobId){
        lastJobId=current; input.value=""; out.textContent=""; out.hidden=true;
      }
      update();
    };
    input.addEventListener("input",update);
    send.addEventListener("click",async()=>{
      sync();
      const job=getJob(), question=input.value.trim();
      if(!question)return;
      const requestJobId=job?.id||null;
      send.disabled=true; notice("正在生成说明…");
      try{
        const endpoint=job?"/api/jobs/"+encodeURIComponent(job.id)+"/ask":"/api/ask";
        const result=await api(endpoint,"POST",JSON.stringify({question,use_model:optIn.checked}));
        // A task switch while the network request was pending must never put
        // an explanation for the old package into the new task's page.
        if((getJob()?.id||null)!==requestJobId)return;
        show(result.answer.text,result.answer.source); notice(result.answer.source.startsWith("fallback:")?"模型不可用，已回退到本地说明。":"说明已生成。");
      }catch(error){notice(error.message||"问助手请求失败");}
      finally{update();}
    });
    sync();
    return {sync};
  }
  window.LazarusAsk={bind};
})();
