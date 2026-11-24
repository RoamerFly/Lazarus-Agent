(function(root){
  function point(rect,naturalWidth,naturalHeight,clientX,clientY,width,height){
    const scale=Math.min(rect.width/naturalWidth,rect.height/naturalHeight);
    const x=(clientX-rect.left-(rect.width-naturalWidth*scale)/2)/(naturalWidth*scale);
    const y=(clientY-rect.top-(rect.height-naturalHeight*scale)/2)/(naturalHeight*scale);
    if(x<0||y<0||x>=1||y>=1)return null;
    return {x:Math.floor(x*width),y:Math.floor(y*height)};
  }
  function bind({api,getJob,isBusy,canPreview,notice,capture}){
    const $=id=>document.getElementById(id),img=$("screen"),select=$("runDevice");
    let selected="emulator",generation=0,frame=null,frameUrl=null,reading=false,pending=0,queue=Promise.resolve(),pointer=null,streaming=false;
    const phone=()=>selected!=="emulator";
    const allowed=()=>phone()||canPreview();
    function sync(busy){
      select.disabled=busy;
      $("refreshDevices").disabled=busy;
      for(const id of ["back","send","refresh"])$(id).disabled=busy||(!frame&&!phone());
      $("captureFeedback").disabled=busy||!frame||!getJob();
      document.querySelector('[data-act="legacy"]').disabled=busy||phone()||!getJob();
      $("previewDeviceLabel").textContent=phone()?"USB 真机 · 当前设备画面":"模拟器 · 当前设备画面";
    }
    async function inventory(){
      const state=await api('/api/devices');
      const keep=state.selected||selected;
      select.replaceChildren();
      for(const device of state.devices){
        const suffix=device.available?"":device.state==='unauthorized'?" · 请在手机授权":" · 已断开";
        const option=new Option(device.label+suffix,device.id);option.disabled=!device.available;select.add(option);
      }
      if(![...select.options].some(o=>o.value===keep))select.add(new Option("USB · 已断开",keep));
      selected=keep;generation=state.generation;select.value=keep;
    }
    select.onchange=async()=>{
      try{
        const state=await api('/api/device','POST',JSON.stringify({device:select.value}));
        selected=state.selected;generation=state.generation;frame=null;img.hidden=true;$("standby").hidden=false;
        $("standby").lastElementChild.textContent="正在连接运行设备…";
        sync(isBusy());await refresh();
        notice(phone()?"已切换 USB 真机。原包/修复包可在此试运行；智能体自动验证使用隔离模拟器。":"已切换模拟器。");
      }catch(error){select.value=selected;notice(error.message,"error");}
    };
    $("refreshDevices").onclick=()=>inventory().catch(error=>notice(error.message,"error"));
    async function refresh(){
      if(reading||!allowed()||document.hidden)return;
      reading=true;const previousGeneration=generation,id=getJob()?.id;
      try{
        const start=performance.now();
        const response=await fetch('/api/preview/screen'+(id?'?job='+encodeURIComponent(id):''),{signal:AbortSignal.timeout(10000)});
        if(!response.ok){const error=await response.json();throw Error(error.detail||"设备画面暂不可用");}
        const blob=await response.blob();
        if(previousGeneration!==generation||getJob()?.id!==id||!allowed())return;
        if(Number(response.headers.get('X-Device-Generation'))!==generation)return;
        streaming=response.headers.get('X-Preview-Mode')==='h264-stream';
        const nextUrl=URL.createObjectURL(blob),old=frameUrl;frameUrl=nextUrl;
        frame={blob,width:Number(response.headers.get('X-Device-Width')),height:Number(response.headers.get('X-Device-Height')),generation};
        img.src=nextUrl;img.hidden=false;$("standby").hidden=true;
        if(old)URL.revokeObjectURL(old);
        $("previewLatency").textContent=Math.round(performance.now()-start)+" ms";
        sync(isBusy());
      }catch(error){
        $("previewLatency").textContent="画面暂不可用";
        if(phone()){frame=null;img.hidden=true;$("standby").hidden=false;$("standby").lastElementChild.textContent=error.message;}
      }finally{reading=false;}
    }
    async function input(body){
      if(isBusy()){notice("自动验证期间暂时锁定设备操作");return;}
      pending++;
      const target=generation,owner=getJob()?.id;
      const task=queue.catch(()=>{}).then(async()=>{
        if(target!==generation)throw Error("运行设备已切换，请重试");
        await api('/api/preview/input','POST',JSON.stringify({...body,job:owner,generation:target}));
        // Refresh immediately after an input, rather than waiting for the timer.
        await refresh();
      }).catch(error=>notice(error.message,"error")).finally(()=>{pending--;});
      queue=task;return task;
    }
    function coordinates(event){
      return frame&&point(img.getBoundingClientRect(),img.naturalWidth,img.naturalHeight,event.clientX,event.clientY,frame.width,frame.height);
    }
    img.onpointerdown=event=>{
      if(isBusy()||!frame)return;
      const at=coordinates(event);if(!at)return;
      pointer={...at,time:performance.now(),generation};img.setPointerCapture(event.pointerId);
      const marker=$("previewTouch"),surface=$("surface").getBoundingClientRect();
      marker.style.left=(event.clientX-surface.left)+"px";marker.style.top=(event.clientY-surface.top)+"px";
      marker.classList.remove('pulse');void marker.offsetWidth;marker.classList.add('pulse');
    };
    img.onpointerup=event=>{
      if(!pointer)return;const from=pointer;pointer=null;const to=coordinates(event);
      if(!to||from.generation!==generation)return;
      if(Math.hypot(to.x-from.x,to.y-from.y)<12)input({event:'tap',x:from.x,y:from.y});
      else input({event:'swipe',x:from.x,y:from.y,x2:to.x,y2:to.y,duration:Math.round(performance.now()-from.time)});
    };
    img.onpointercancel=()=>{pointer=null;};
    img.ondragstart=event=>event.preventDefault();
    $("captureFeedback").onclick=()=>{
      if(!frame)return;
      const reader=new FileReader();reader.onload=()=>capture(reader.result);reader.readAsDataURL(frame.blob);
    };
    async function loop(){
      const start=performance.now();
      if(!pending)await refresh();
      setTimeout(loop,document.hidden?1000:Math.max(25,(streaming?90:350)-(performance.now()-start)));
    }
    inventory().then(()=>sync(isBusy())).catch(()=>{});
    setInterval(()=>{if(!pending&&!isBusy()&&!document.hidden&&document.activeElement!==select)inventory().catch(()=>{});},6000);
    loop();
    return {refresh,input,sync,phone};
  }
  root.LazarusPreview={bind,point};
  if(typeof module!=="undefined")module.exports={point};
})(typeof window!=="undefined"?window:globalThis);
