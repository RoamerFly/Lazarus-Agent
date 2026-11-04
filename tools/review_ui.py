import sys,os,time,threading,json,itertools
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['LAZARUS_STORAGE_ROOT']=str(Path('build/ui-review/storage').resolve())
os.environ['REVIVER_PORT']='18767'
import uvicorn,workbench
from playwright.sync_api import sync_playwright
out=Path('artifacts/ui-review');out.mkdir(parents=True,exist_ok=True)
mode=sys.argv[1]
server=uvicorn.Server(uvicorn.Config(workbench.app,host='127.0.0.1',port=18767,log_level='error',access_log=False))
t=threading.Thread(target=server.run,daemon=True);t.start()
while not server.started:time.sleep(.1)
checks=[]
try:
 with sync_playwright() as pw:
  browser=pw.chromium.launch(channel='msedge',headless=True)
  page=browser.new_page()
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.route('**/api/updates/check*',lambda r:r.fulfill(json={'available':False,'current_version':'0.6.4'}))
  page.route('**/api/jobs',lambda r:r.fulfill(json=[]))
  for width,height in [(375,900),(768,900),(960,640),(1024,768),(1280,640),(1280,720),(1280,900),(1920,1080)]:
   page.set_viewport_size({'width':width,'height':height})
   page.goto('http://127.0.0.1:18767');page.wait_for_function('!document.getElementById("file").disabled')
   page.screenshot(animations='disabled',path=str(out/f'{mode}-{width}x{height}.png'),full_page=True)
   record={'width':width,'height':height,'page_vertical_overflow':page.evaluate('document.documentElement.scrollHeight>innerHeight'),'overflows':page.evaluate('document.documentElement.scrollWidth>innerWidth'), 'header_overlaps':page.evaluate('''()=>{const es=[...document.querySelectorAll('header .brand,header nav,header > .row')];let a=[];for(let i=0;i<es.length;i++)for(let j=i+1;j<es.length;j++){let r=es[i].getBoundingClientRect(),s=es[j].getBoundingClientRect();if(Math.min(r.right,s.right)-Math.max(r.left,s.left)>1&&Math.min(r.bottom,s.bottom)-Math.max(r.top,s.top)>1)a.push([es[i].className||es[i].tagName,es[j].className||es[j].tagName]);}return a}''')}
   # Stress only the visible DOM. These strings are layout fixtures, not repair output.
   page.evaluate('''()=>{document.getElementById('appname').textContent='恐龙战争：旧版应用兼容性与完整中文测试安装包';document.getElementById('arch').textContent='armeabi-v7a · arm64-v8a · x86_64';document.getElementById('attachmentName').textContent='Android16修复后点击继续按钮出现异常的完整截图.png';document.getElementById('versions').innerHTML='<div class="version"><strong>修复版本 V12 · Android 14 / Android 15 / Android 16</strong><span class="pill">等待真机复测</span><a href="#">下载 V12</a></div>';document.getElementById('messages').innerHTML='<div class="bubble">'+('已核对安装包及字体，等待目标设备验证。'.repeat(9))+'<br>输出路径：E:/very_long_resource_path/'+('candidate_'.repeat(20))+'.apk</div>';document.querySelector('.diagnostics').open=true;document.getElementById('facts').innerHTML='<div class="fact"><span>签名证书</span><span>'+('ab'.repeat(64))+'</span></div>'}''')
   record['stress_overflow']=page.evaluate('document.documentElement.scrollWidth>innerWidth')
   page.screenshot(animations='disabled',path=str(out/f'{mode}-{width}x{height}-stress.png'),full_page=True)
   record['card_overlaps']=page.evaluate("""()=>{
    const bad=[];
    for(const card of document.querySelectorAll('article.card')){
      const boxes=[...card.children].filter(e=>getComputedStyle(e).display!=='none').map(e=>[e,e.getBoundingClientRect()]).filter(([e,r])=>r.width&&r.height);
      for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){
       const [a,r]=boxes[i],[b,s]=boxes[j];
       if(Math.min(r.right,s.right)-Math.max(r.left,s.left)>1 && Math.min(r.bottom,s.bottom)-Math.max(r.top,s.top)>1)bad.push([a.id||a.className,b.id||b.className]);
      }
    }return bad;
   }""")
   record['dialogs']={}
   for name,button,close in [('model','modelSettingsButton','closeModelSettings'),('resources','resourcesButton','closeResources'),('about','aboutButton',None),('history','history', 'closeHistory'),('scope','scopeSummary',None),('delivery','[data-open-dialog=deliveryDialog]',None),('diagnostics','[data-open-dialog=diagnosticsDialog]',None),('assistant','[data-open-dialog=assistantDialog]',None),('task','[data-open-dialog=taskDialog]',None),('modification','[data-open-dialog=modificationDialog]',None)]:
    page.locator(button if button.startswith('[') else '#'+button).click()
    dialog=page.locator('dialog[open]');dialog.wait_for()
    record['dialogs'][name]=dialog.evaluate('(e)=>({width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height,ratio:e.getBoundingClientRect().width/e.getBoundingClientRect().height,scroll:e.scrollWidth>e.clientWidth,clipped:e.getBoundingClientRect().right>innerWidth})')
    page.screenshot(animations='disabled',path=str(out/f'{mode}-{width}x{height}-{name}.png'))
    page.keyboard.press('Escape')
   checks.append(record)
  page.unroute('**/api/jobs')
  page.route('**/api/jobs',lambda r:r.fulfill(json=[{'id':str(i),'label':'记录布局测试 '+str(i),'api':36,'created':'2026-11-04T00:00:00Z'} for i in range(40)]))
  page.locator('#history').click()
  assert page.locator('#jobList > button').count()==40
  assert not page.locator('#jobList').evaluate('(e)=>e.classList.contains("empty-state")')
  page.screenshot(animations='disabled',path=str(out/f'{mode}-history-filled.png'))
  page.keyboard.press('Escape')
  # Preset tests intercept only the local settings API; no provider receives a key.
  submitted=[]
  stored={'url':'','model':'','has_key':False,'enabled':False,'source':'none'}
  def model_api(route):
   if route.request.method=='GET':route.fulfill(json=stored);return
   data=route.request.post_data_json;submitted.append(data)
   if route.request.url.endswith('/test'):route.fulfill(json={'message':'本地测试桩验证完成'});return
   stored.update(url=data['url'],model=data['model'],has_key=bool(data.get('key')),enabled=data['enabled'],source='saved')
   route.fulfill(json=stored)
  page.route('**/api/model/settings',model_api)
  page.route('**/api/model/test',model_api)
  page.locator('#modelSettingsButton').click();page.wait_for_function('!document.getElementById("modelProvider").disabled')
  page.locator('#modelProvider').select_option('deepseek')
  assert page.locator('#modelUrl').input_value()=='https://api.deepseek.com'
  assert page.locator('#modelName').input_value()=='deepseek-flash'
  page.screenshot(animations='disabled',path=str(out/f'{mode}-deepseek-preset.png'))
  page.locator('#modelKey').fill('test-only-draft')
  page.locator('#modelTest').click();page.wait_for_function('!document.getElementById("modelTest").disabled')
  page.locator('#modelProvider').select_option('openai')
  assert page.locator('#modelKey').input_value()==''
  assert page.locator('#modelUrl').input_value()=='https://api.openai.com/v1'
  page.locator('#modelName').fill('custom-unit-model')
  page.locator('#modelTest').click();page.wait_for_function('!document.getElementById("modelTest").disabled')
  assert submitted[-1]['key']=='', 'A tested draft key crossed provider boundary'
  page.locator('#modelProvider').select_option('custom')
  page.locator('#modelUrl').fill('http://127.0.0.1:12345/v1')
  page.locator('#modelName').fill('local-unit-model')
  page.locator('#modelSave').click();page.wait_for_function('!document.getElementById("modelSave").disabled')
  page.keyboard.press('Escape');page.locator('#modelSettingsButton').click()
  page.wait_for_function('!document.getElementById("modelProvider").disabled')
  assert page.locator('#modelProvider').input_value()=='custom'
  assert page.locator('#modelName').input_value()=='local-unit-model'
  page.keyboard.press('Escape')
  page.unroute('**/api/model/settings',model_api);page.unroute('**/api/model/test',model_api)
  # Verify incremental rendering and scroll retention using a synthetic chat job.
  page.set_viewport_size({'width':1280,'height':height})
  page.goto('http://127.0.0.1:18767');page.wait_for_function('!document.getElementById("file").disabled')
  chat_checks=page.evaluate("""()=>{
    job={id:'ui-review-only',package:{native_abis:[],label:'布局测试'},versions:[],messages:Array.from({length:18},(_,i)=>({role:'assistant',text:'布局测试消息 '+i+'：核对字体与中文显示。'.repeat(8)}))};
    renderChat(false);const chat=document.getElementById('messages'),first=chat.firstElementChild;
    chat.scrollTop=0;job.messages.push({role:'assistant',text:'追加消息'});renderChat(false);
    const retained=chat.firstElementChild===first,readingOffset=chat.scrollTop;
    chat.scrollTop=chat.scrollHeight;job.messages.push({role:'assistant',text:'自动跟随最新消息'});renderChat(false);
    return {retained,readingOffset,followsEnd:chat.scrollHeight-chat.scrollTop-chat.clientHeight<2};
  }""")
  # Import a signed fixture through the normal file picker and render the real API state.
  page.goto('http://127.0.0.1:18767');page.wait_for_function('!document.getElementById("file").disabled')
  page.locator('#file').set_input_files(str(Path('fixtures/archive-reader/archive-reader-broken.apk').resolve()))
  page.wait_for_function('job!==null && !!job.assessment',timeout=60000)
  render_check=page.locator('#badge').inner_text()
  assert render_check=='就绪',render_check
  page.locator('#message').fill('记住上次打开的文件')
  page.locator('[data-open-dialog=modificationDialog]').click()
  page.locator('#toModification').click()
  assert page.locator('#modificationRequest').input_value()=='记住上次打开的文件'
  page.keyboard.press('Escape')
  requests=[]
  state=page.evaluate('job')
  def message_api(route):
   requests.append(route.request.post_data_json);route.fulfill(json=state)
  page.route('**/api/jobs/*/message',message_api)
  page.locator('#message').fill('汉化成中文');page.locator('#sendMessage').click()
  page.wait_for_function('!document.getElementById("sendMessage").disabled')
  assert requests[-1]['use_configured_model'] is True
  assert 'translate_with_model' not in requests[-1]
  assert page.locator('[data-open-dialog=translationDialog]').count()==0
  page.unroute('**/api/jobs/*/message',message_api)
  page.locator('#problem').click()
  assert page.locator('#taskDialog').evaluate('(e)=>e.open')
  page.locator('#taskTap').fill('继续')
  page.keyboard.press('Escape')
  page.locator('[data-open-dialog=taskDialog]').click()
  assert page.locator('#taskTap').input_value()=='继续'
  page.keyboard.press('Escape')
  page.emulate_media(reduced_motion='reduce')
  motion=page.locator('.bubble').first.evaluate('(e)=>getComputedStyle(e).animationName')
  page.locator('#modelSettingsButton').focus()
  focus=page.locator('#modelSettingsButton').evaluate('(e)=>getComputedStyle(e).outlineStyle')
  assert chat_checks['retained'] and chat_checks['readingOffset']==0 and chat_checks['followsEnd'],chat_checks
  assert motion=='none' and focus!='none',(motion,focus)
  if mode!='before':
   assert not errors,errors
   assert all(not c['page_vertical_overflow'] for c in checks if c['width']>=960),checks
   assert all(not c['overflows'] and not c['stress_overflow'] and not c['header_overlaps'] and not c['card_overlaps'] for c in checks),checks
   assert all(not d['scroll'] and not d['clipped'] and d['ratio']<2.6 for c in checks for d in c['dialogs'].values())
  (out/f'{mode}.json').write_text(json.dumps({'checks':checks,'errors':errors,'chat':chat_checks,'reduced_motion':motion,'focus_outline':focus,'complete_render':render_check,'provider_checks':'preset, custom edit, save/reopen, draft-key isolation passed (local API stubs)','history_cases':'empty and 40 rows passed','unified_chat':True},ensure_ascii=False,indent=2),encoding='utf8')
  browser.close()
finally:server.should_exit=True;t.join()


