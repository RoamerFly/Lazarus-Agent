const {test}=require('node:test');
const assert=require('node:assert/strict');
const {result}=require('../web/run-status.js');
const base={status:'ready',action:'original',package:{package:'app'},tests:[]};
const passed={file:'原包',install:{exit:0},launch:{exit:0,focused_package:'app'}};
test('completed install rejection is red and never exposes launcher preview',()=>{
  const r=result({...base,tests:[{file:'原包',install:{exit:1,output:'INSTALL_FAILED_DEPRECATED_SDK_VERSION'}}]});
  assert.equal(r.tone,'error');assert.equal(r.preview,false);assert.match(r.title,/安装失败/);
});
test('successful am start without app window is not green success',()=>{
  assert.equal(result({...base,tests:[{...passed,launch:{exit:0,focused_package:'launcher'}}]}).tone,'warning');
  assert.equal(result({...base,tests:[{...passed,runtime:{java_crash:true}}]}).tone,'error');
});
test('green launch result has bounded wording and supports preview',()=>{
  const r=result({...base,tests:[passed]});
  assert.equal(r.tone,'success');assert.equal(r.preview,true);assert.match(r.detail,/功能仍需/);
});
test('new operation does not reuse previous successful test',()=>{
  const r=result({...base,status:'running',run_test_offset:1,tests:[passed]});
  assert.equal(r.tone,'busy');assert.equal(r.preview,false);
});
test('repair launch without accepted artifact does not become green',()=>{
  assert.equal(result({...base,action:'repair',tests:[passed]}).tone,'warning');
});
test('feature acceptance keeps launch and behavior verification distinct',()=>{
  const job={...base,action:'chat-feature',latest_feature_candidate:'id',feature_candidates:{id:{acceptance:{status:'behavior-verified'}}}};
  assert.equal(result(job).title,'功能验证通过');
  job.feature_candidates.id.acceptance.status='failed';assert.equal(result(job).tone,'error');
});
