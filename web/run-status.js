/* A finished operation is not necessarily a successful application run. */
(function(root){
  function result(job){
    if(!job)return {tone:"neutral",title:"等待导入 APK",detail:"",preview:false};
    const tests=job.tests||[], active=Number.isInteger(job.run_test_offset)?tests.slice(job.run_test_offset):tests;
    const test=active.at(-1), label=test?.file||"应用";
    if(job.status==="running")return {tone:"busy",title:"正在执行",detail:job.events?.at(-1)?.text||"正在准备运行环境…",preview:!!test&&test.install?.exit===0};
    if(job.status==="error")return {tone:"error",title:"执行失败",detail:job.events?.at(-1)?.text||"请查看运行记录。",preview:false};
    if(["original","legacy","candidate","repair"].includes(job.action)&&test){
      if(test.install?.exit!==0){
        const output=test.install?.output||"",code=output.match(/INSTALL_FAILED_[A-Z_]+/)?.[0];
        const reason=code==="INSTALL_FAILED_DEPRECATED_SDK_VERSION"?"目标 SDK 过低，被 Android 安装策略拒绝。":code==="INSTALL_FAILED_NO_MATCHING_ABIS"?"应用架构与测试设备不匹配。":code||"请查看运行记录中的安装错误。";
        return {tone:"error",title:label+"安装失败",detail:reason+" 应用未启动，无法显示应用预览。",preview:false};
      }
      if(test.runtime?.java_crash||test.runtime?.native_crash||test.launch?.exit!==0)return {tone:"error",title:label+"启动失败",detail:"安装已完成，但未成功启动或检测到崩溃。请查看运行记录。",preview:false};
      if(test.launch?.focused_package!==job.package?.package)return {tone:"warning",title:"已安装，待确认应用界面",detail:"当前尚未确认目标应用窗口。预览仅表示设备画面。",preview:true};
      if(job.action==="repair"&&!job.export)return {tone:"warning",title:"启动通过，修复尚未验收",detail:"未产生已验证交付版本，请查看运行记录。",preview:true};
      return {tone:"success",title:label+"安装、启动通过",detail:job.export?.level||"已观察到应用窗口；具体功能仍需操作验证。",preview:true};
    }
    const candidate=job.feature_candidates?.[job.latest_feature_candidate];
    const acceptance=candidate?.acceptance?.status;
    if(acceptance==="behavior-verified")return {tone:"success",title:"功能验证通过",detail:"通过了本次声明的验收步骤，其他功能仍需真机确认。",preview:true};
    if(acceptance==="launch-verified")return {tone:"success",title:"安装、启动通过",detail:"功能尚未通过完整验收。",preview:true};
    if(acceptance==="failed"||acceptance==="incomplete")return {tone:"error",title:"候选验证未通过",detail:"请查看对话中的验收结果；可能是安装、运行失败或验证环境不可用。",preview:false};
    return {tone:"neutral",title:"待运行验证",detail:"导入完成，尚无本轮运行结果。",preview:false};
  }
  root.LazarusRunStatus={result};
  if(typeof module!=="undefined")module.exports={result};
})(typeof window!=="undefined"?window:globalThis);
