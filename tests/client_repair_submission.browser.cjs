// Isolated API fixtures against the built client; no business database or devices.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const root=path.resolve(__dirname,'../app/static/client');
const server=http.createServer((req,res)=>{const file=req.url.startsWith('/client-assets/')?path.join(root,req.url.replace('/client-assets/','')):path.join(root,'index.html');try{res.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(file))}catch{res.statusCode=404;res.end()}});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE,args:['--no-sandbox','--disable-dev-shm-usage']});try{
for(const width of [390,1440,320]){
 const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});const errors=[];page.on('pageerror',e=>errors.push(e.message));let failOptions=true,failCreate=true,creates=0,uploads=0,releaseUpload=null;
 await page.route('**/api/**',async route=>{const u=new URL(route.request().url()).pathname;let data={};
 if(u.endsWith('/auth/me'))data={id:1,username:'fixture'};
 else if(u==='/api/branding')data={brand_name:'XS 服务'};
 else if(u.endsWith('/devices')){if(failOptions){failOptions=false;return route.fulfill({status:503,json:{error:{message:'读取失败，请重试'}}})}data=[]}
 else if(u.endsWith('/addresses'))data=[];
 else if(u.endsWith('/work-items'))data=[];
 else if(u.endsWith('/repair/42'))data={id:42,order_no:'WX-TEST-42',status:'awaiting_device',status_label:'等待收件',device:{brand:'DJI',model:'Neo'},timeline:[]};
 else if(u.endsWith('/repair')){creates++;if(failCreate){failCreate=false;return route.fulfill({status:422,json:{error:{message:'请检查申请信息后重试'}}})}data={id:42,order_no:'WX-TEST-'+ '1234567890'.repeat(6),status_label:'等待收件'}}
 else if(u.endsWith('/attachments')){uploads++;if(uploads===1)await new Promise(resolve=>{releaseUpload=resolve});await new Promise(r=>setTimeout(r,400));if(uploads===1)return route.fulfill({status:503,json:{error:{message:'未能确认上传结果'}}});data={id:uploads}}
 return route.fulfill({json:{success:true,data}})});
 await page.goto(`http://127.0.0.1:${server.address().port}/client/repair/new`);
 await page.getByRole('button',{name:'重新读取'}).click();await page.locator('form').waitFor();
 assert(await page.getByRole('button',{name:'提交维修申请'}).isDisabled());assert(await page.getByRole('link',{name:'“我的”添加地址'}).isVisible());
 // Form labels provide real accessible names, and native validation focuses the first missing field.
 await page.getByLabel('服务方式').selectOption('store');
 await page.getByRole('button',{name:'提交维修申请'}).click();
 assert.equal(await page.evaluate(()=>document.activeElement.id),'repair-model');assert.equal(creates,0);
 await page.getByLabel('型号').fill('Neo 超长设备型号测试');
 // Canceling navigation keeps both text and selected attachments intact.
 await page.locator('input[type=file]').setInputFiles({name:'保留.png',mimeType:'image/png',buffer:Buffer.from('fixture')});
 page.once('dialog',async d=>{assert(d.message().includes('尚未提交'));await d.dismiss()});
 await page.getByLabel('服务方式').selectOption('shipping');
 await page.getByRole('link',{name:'“我的”添加地址'}).click();
 assert(new URL(page.url()).pathname.endsWith('/repair/new'));assert.equal(await page.getByLabel('型号').inputValue(),'Neo 超长设备型号测试');assert.equal(await page.locator('.repair-file-list li').count(),1);
 const prevented=await page.evaluate(()=>!window.dispatchEvent(new Event('beforeunload',{cancelable:true})));assert(prevented);
 await page.locator('.repair-file-remove').click();
 await page.getByLabel('服务方式').selectOption('store');
 await page.locator('label[for="repair-description"]').click();assert.equal(await page.evaluate(()=>document.activeElement.id),'repair-description');
 assert.equal(await page.getByLabel('故障描述').getAttribute('aria-describedby'),'repair-description-help');
 for(const control of await page.locator('form input,form select,form textarea').all())assert(await control.evaluate(el=>el.labels?.length>0));
 assert.equal(await page.locator('form > fieldset').count(),4);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 if(process.env.SCREENSHOT_DIR){await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/repair-form-${width}.png`,fullPage:true});await page.evaluate(()=>document.documentElement.dataset.theme='dark');await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/repair-form-dark-${width}.png`,fullPage:true});await page.evaluate(()=>document.documentElement.dataset.theme='light')}
 await page.evaluate(()=>window.scrollTo(0,0));
 if(process.env.SCREENSHOT_DIR)await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/repair-form-top-${width}.png`});
 await page.getByRole('button',{name:'提交维修申请'}).evaluate(el=>el.scrollIntoView({block:'center'}));
 const submitBox=await page.getByRole('button',{name:'提交维修申请'}).boundingBox(),bottomNav=await page.locator('.bottom-nav').boundingBox();if(bottomNav)assert(submitBox.y+submitBox.height<=bottomNav.y);
 if(process.env.SCREENSHOT_DIR)await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/repair-form-bottom-${width}.png`});
 const inputs=page.locator('form input:not([type])');await inputs.nth(1).fill('Neo 超长设备型号测试');await inputs.nth(2).fill('TEST12345');await inputs.nth(3).fill('无法开机');await page.locator('textarea').first().fill('测试数据：设备无法正常开机');
 await page.locator('select').nth(1).selectOption('store');await inputs.nth(6).fill('测试客户');await page.locator('input[inputmode="tel"]').fill('13800000000');
 // Selection is visible and recoverable before any order is created.
 await page.locator('input[type=file]').setInputFiles(Array.from({length:13},(_,i)=>({name:`测试附件-${i}.png`,mimeType:'image/png',buffer:Buffer.from('fixture')})));
 assert.equal(await page.locator('.repair-file-list li').count(),13);assert(await page.getByRole('button',{name:'提交维修申请'}).isDisabled());assert.equal(creates,0);
 await page.locator('.repair-file-remove').last().click();assert.equal(await page.locator('.repair-file-list li').count(),12);assert(await page.getByRole('button',{name:'提交维修申请'}).isEnabled());
 await page.locator('input[type=file]').setInputFiles({name:'恰好上限.png',mimeType:'image/png',buffer:Buffer.alloc(10*1024*1024)});assert(await page.getByRole('button',{name:'提交维修申请'}).isEnabled());
 await page.locator('input[type=file]').setInputFiles([{name:'超出限制的照片.png',mimeType:'image/png',buffer:Buffer.alloc(10*1024*1024+1)},{name:'不支持的长文件名'.repeat(12)+'.gif',mimeType:'image/gif',buffer:Buffer.from('fixture')}]);
 assert.equal(await page.locator('.repair-file-list .repair-file-error').count(),2);assert(await page.getByRole('button',{name:'提交维修申请'}).isDisabled());assert.equal(creates,0);
 await page.locator('.repair-file-list').evaluate(el=>el.scrollIntoView({block:'center'}));
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 if(process.env.SCREENSHOT_DIR){await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/attachment-errors-${width}.png`});await page.evaluate(()=>document.documentElement.dataset.theme='dark');await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/attachment-errors-dark-${width}.png`});await page.evaluate(()=>document.documentElement.dataset.theme='light')}
 await page.locator('.repair-file-remove').first().click();assert.equal(await page.evaluate(()=>document.activeElement?.className),'repair-file-remove');
 await page.keyboard.press('Enter');assert.equal(await page.evaluate(()=>document.activeElement?.id),'repair-files');assert.equal(await page.locator('.repair-file-list li').count(),0);assert(await page.getByRole('button',{name:'提交维修申请'}).isEnabled());
 await page.locator('input[type=file]').setInputFiles({name:'empty.png',mimeType:'image/png',buffer:Buffer.alloc(0)});assert(await page.getByText('文件为空，请重新选择。').isVisible());await page.locator('.repair-file-remove').click();
 await page.locator('input[type=file]').setInputFiles([{name:'未确认的长文件名'.repeat(10)+'.png',mimeType:'image/png',buffer:Buffer.from('fixture')},{name:'success.png',mimeType:'image/png',buffer:Buffer.from('fixture')}]);
 await page.getByRole('button',{name:'提交维修申请'}).click();await page.getByText('请检查申请信息后重试').waitFor();assert.equal(await inputs.nth(1).inputValue(),'Neo 超长设备型号测试');
 assert.equal(await page.evaluate(()=>document.activeElement.className),'repair-submit-error');assert.equal(await page.locator('.repair-file-list li').count(),2);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 if(process.env.SCREENSHOT_DIR){await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/submit-error-${width}.png`});await page.evaluate(()=>document.documentElement.dataset.theme='dark');await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/submit-error-dark-${width}.png`});await page.evaluate(()=>document.documentElement.dataset.theme='light')}
 await page.getByRole('button',{name:'返回检查填写内容'}).focus();await page.keyboard.press('Enter');assert.equal(await page.evaluate(()=>document.activeElement.id),'repair-device');assert.equal(creates,1);

 await page.getByRole('button',{name:'提交维修申请'}).click();await page.getByRole('heading',{name:'维修申请已提交'}).waitFor();assert.equal(await page.evaluate(()=>document.activeElement.id),'repair-result-title');assert(await page.getByRole('button',{name:'附件上传中…'}).isDisabled());
 page.once('dialog',async d=>{assert(d.message().includes('正在上传'));await d.dismiss()});
 await page.locator('a[href="/client/work-orders"]:visible').first().click();
 assert(new URL(page.url()).pathname.endsWith('/repair/new'));assert.equal(typeof releaseUpload,'function');releaseUpload();
 await page.getByRole('link',{name:'查看服务进度'}).waitFor();assert.equal(creates,2);assert.equal(uploads,2);assert(await page.evaluate(()=>window.dispatchEvent(new Event('beforeunload',{cancelable:true}))));assert(await page.getByText('已确认上传 1 / 2 个附件').isVisible());assert(await page.getByText('以下附件未能确认上传成功。',{exact:false}).isVisible());
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 if(process.env.SCREENSHOT_DIR)await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/repair-result-${width}.png`,fullPage:true});
 await page.evaluate(()=>document.documentElement.dataset.theme='dark');
 if(process.env.SCREENSHOT_DIR)await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/repair-result-dark-${width}.png`,fullPage:true});
 await page.getByRole('link',{name:'查看服务进度'}).evaluate(el=>el.scrollIntoView({block:'center'}));
 const action=await page.getByRole('link',{name:'查看服务进度'}).boundingBox(),nav=await page.locator('.bottom-nav').boundingBox();if(nav)assert(action.y+action.height<=nav.y);
 if(process.env.SCREENSHOT_DIR)await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/repair-action-${width}.png`});
 await page.getByRole('link',{name:'查看服务进度'}).focus();await page.keyboard.press('Enter');await page.waitForURL('**/client/work-orders?repair=42');await page.getByRole('heading',{name:'等待收件',exact:true}).waitFor();await page.goBack();await page.locator('form').waitFor();assert.equal(creates,2);
 for(const withFile of [false,true]){
  await inputs.nth(1).fill('Neo');await inputs.nth(2).fill('TEST12345');await inputs.nth(3).fill('无法开机');await page.locator('textarea').first().fill('测试设备无法正常开机');await page.locator('select').nth(1).selectOption('store');await inputs.nth(6).fill('测试客户');await page.locator('input[inputmode="tel"]').fill('13800000000');
  if(withFile)await page.locator('input[type=file]').setInputFiles({name:'success.png',mimeType:'image/png',buffer:Buffer.from('fixture')});
  await page.getByRole('button',{name:'提交维修申请'}).click();await page.getByRole('link',{name:'查看服务进度'}).waitFor();assert(await page.getByText(withFile?'已确认上传 1 / 1 个附件':'本次未选择附件').isVisible());assert.equal(await page.locator('.repair-upload-warning').count(),0);
  await page.goto(`http://127.0.0.1:${server.address().port}/client/repair/new`);await page.locator('form').waitFor();
 }
 assert.equal(creates,4);assert.equal(uploads,3);
 // Untouched defaults are clean; explicit confirmation discards edits and allows navigation.
 assert(await page.evaluate(()=>window.dispatchEvent(new Event('beforeunload',{cancelable:true}))));
 await page.getByLabel('型号').fill('确认离开测试');
 page.once('dialog',async d=>{assert(d.message().includes('尚未提交'));await d.accept()});
 await page.getByRole('link',{name:'“我的”添加地址'}).click();await page.waitForURL('**/client/profile');
 assert(await page.evaluate(()=>window.dispatchEvent(new Event('beforeunload',{cancelable:true}))));

 assert.deepEqual(errors,[]);console.log(`PASS ${width}px: options retry, no address guidance, create error retains input, partial upload continues, visible result, focus, navigation, long content`);await page.close();
}
}finally{await browser.close();server.close()}})().catch(e=>{console.error(e);server.close();process.exitCode=1});
