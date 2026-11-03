function bindResources(){
  const dialog=el("resourcesDialog");
  el("closeResources").onclick=()=>dialog.close();
  el("resourcesButton").onclick=async()=>{
    try{
      const resources=await api("/api/resources");
      const list=el("resourceList");
      list.replaceChildren();
      for(const entry of resources.locations){
        const row=document.createElement("section"), label=document.createElement("strong"), value=document.createElement("code");
        row.className="resource-location";
        label.textContent=entry.label;
        value.textContent=entry.path;
        row.append(label,value);
        if(entry.available){
          const open=document.createElement("button");
          open.textContent="打开目录";
          open.onclick=async()=>{try{await api("/api/resources/open/"+entry.id,"POST");}catch(e){notice(e.message);}};
          row.append(open);
        }
        list.append(row);
      }
      for(const item of resources.tools){
        const line=document.createElement("p");
        line.textContent=(item.exists?"已找到":"未找到")+" · "+item.name+" · "+item.path;
        line.className="scopehint";list.append(line);
      }
      if(resources.summary){
        const summary=document.createElement("p");summary.textContent=resources.summary;list.prepend(summary);
      }
      dialog.showModal();
    }catch(e){notice(e.message);}
  };
}
