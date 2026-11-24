const {test}=require('node:test');
const assert=require('node:assert/strict');
const {result}=require('../web/run-status.js');
const {createSession}=require('../web/run-status.js');
const base={status:'ready',action:'original',package:{package:'app'},tests:[]};
const passed={file:'原包',install:{exit:0},launch:{exit:0,focused_package:'app'}};
test('reopening failed job shows neutral state while keeping red history intact',()=>{
  const session=createSession(),job={...base,id:'restored',tests:[{install:{exit:1,output:'INSTALL_FAILED_DEPRECATED_SDK_VERSION'}}]};
  const original=JSON.stringify(job);session.restore(job);
  for(let poll=0;poll<5;poll++)assert.equal(session.main({...job}).tone,'neutral');
  assert.equal(session.main(job).preview,false);
  assert.match(session.diagnostic(job).title,/历史记录/);
  assert.equal(session.diagnostic(job).tone,'error');
  assert.equal(JSON.stringify(job),original);
});
test('reopening successful job does not imply app is running in new session',()=>{
  const session=createSession(),job={...base,id:'success',tests:[passed]};session.restore(job);
  assert.equal(session.main(job).tone,'neutral');assert.equal(session.main(job).preview,false);
  assert.equal(session.diagnostic(job).tone,'success');
});
test('accepted current operation displays failure even if it finishes before next poll',()=>{
  const session=createSession(),job={...base,id:'current',tests:[{install:{exit:1}}]};session.restore(job);
  session.started(job);assert.equal(session.main(job).tone,'error');
  assert.doesNotMatch(session.diagnostic(job).title,/历史记录/);
});
test('reopening active operation keeps progress and subsequent actual result',()=>{
  const session=createSession(),job={...base,id:'active',status:'running',tests:[]};session.restore(job);
  assert.equal(session.main(job).tone,'busy');
  assert.equal(session.main({...job,status:'ready',tests:[passed]}).tone,'success');
});
test('a run observed after restore becomes current and stays current when selected again',()=>{
  const session=createSession(),job={...base,id:'observed',tests:[]};session.restore(job);
  session.main({...job,status:'running'});
  session.restore(job);
  assert.equal(session.main({...job,status:'error',events:[{text:'new failure'}]}).tone,'error');
});
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
