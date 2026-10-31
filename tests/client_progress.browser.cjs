// Built-client browser regression with isolated API fixtures; no live customer data.
// npm run build --prefix client; provide PLAYWRIGHT_MODULE and CHROMIUM_EXECUTABLE if needed.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const http=require('node:http');
const root=path.resolve(__dirname,'../app/static/client');
const server=http.createServer((req,res)=>{const file=req.url.startsWith('/client-assets/')?path.join(root,req.url.replace('/client-assets/','')):path.join(root,'index.html');try{res.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(file))}catch{res.statusCode=404;res.end()}});
const date='2026-10-28T10:00:00Z';
const items=[['quoted','报价待确认'],['repairing','维修中'],['completed','已完成']].map(([status,label],i)=>({key:`repair:${i+1}`,type:'repair',type_label:'维修工单',id:i+1,number:`WX-TEST-00${i+1}`,title:['DJI Neo','DJI Avata 2','DJI Mic 2'][i],summary:'设备检测',status,status_label:label,created_at:date,updated_at:date}));
function detail(id){const item=items[id-1];return {...item,order_no:item.number,device:{brand:'DJI',model:item.title.replace('DJI ',''),serial_number:'TEST123456789'},service_mode:'shipping',contact_name:'测试客户',fault_description:'机身损坏，请检测维修。',timeline:[{status:'received',label:'设备已收到',time:date},{status:item.status,label:item.status_label,time:date}],current_quote:{id:10,quote_no:'Q-TEST-001',version:1,status:'sent',subtotal:'129.00',labor_fee:'60.00',shipping_fee:'0.00',discount:'0.00',total_amount:'189.00',assessment_result:'机身外壳损坏，建议更换。',repair_recommendation:'更换外壳并进行测试。',customer_notice:'请核对维修项目和费用。',items:[{id:1,name:'机身主体（含动力组件）',quantity:1,unit_price:'129.00',amount:'129.00'}]}}}
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE,args:['--no-sandbox','--disable-dev-shm-usage']});try{
for(const width of [390,1440,320]){
const page=await browser.newPage({viewport:{width,height:900}});const errors=[];page.on('pageerror',e=>errors.push(e.message));let failList=true,failDetail=false,decisions=0;let emptyAccount=false;let pdfRequests=0;let current=detail(1);let detailRequests=0;let holdDetail=false,releaseDetail=null;
await page.route('**/api/**',async route=>{const url=new URL(route.request().url()).pathname;let data={};if(url.endsWith('/devices')||url.endsWith('/addresses'))data=[];else if(url.endsWith('/auth/me'))data={id:1,username:'test'};else if(url==='/api/branding')data={brand_name:'XS 服务'};else if(url.endsWith('/work-items')){if(failList){failList=false;return route.fulfill({status:503,json:{error:{message:'测试读取失败'}}})}data=emptyAccount?[]:items}else if(url.endsWith('/quote-decision')){decisions++;await new Promise(r=>setTimeout(r,150));current={...current,status:'customer_confirmed',status_label:'报价已确认',current_quote:{...current.current_quote,status:'confirmed'}};data=current}else if(url.endsWith('/pdf')){pdfRequests++;await new Promise(r=>setTimeout(r,300));if(pdfRequests===1)return route.fulfill({status:503,json:{error:{message:'fixture download failure'}}});return route.fulfill({contentType:'application/pdf',body:'%PDF-1.4\n%%EOF'})}else if(/\/repair\/\d+$/.test(url)){detailRequests++;if(holdDetail)await new Promise(resolve=>{releaseDetail=resolve});if(url.endsWith('/999'))return route.fulfill({status:404,json:{error:{message:'未找到可访问的维修工单'}}});if(failDetail){failDetail=false;return route.fulfill({status:503,json:{error:{message:'详情读取失败'}}})}data=url.endsWith('/1')?current:detail(Number(url.split('/').pop()))}return route.fulfill({json:{success:true,data}})});
await page.goto(`http://127.0.0.1:${server.address().port}/client/work-orders`);
await page.getByRole('button',{name:'重新读取'}).click();await page.locator('.service-case').first().waitFor();assert.equal(await page.locator('.service-case').count(),3);
await page.getByRole('button',{name:'待处理',exact:true}).click();assert.equal(await page.locator('.service-case').count(),1);
await page.getByRole('button',{name:'历史案例',exact:true}).click();assert.equal(await page.locator('.service-case').count(),1);
await page.getByRole('button',{name:'全部案例',exact:true}).click();await page.getByRole('searchbox').fill('NOTFOUND');assert.equal(await page.locator('.service-case').count(),0);await page.getByRole('searchbox').fill('');
await page.getByRole('button',{name:'商城',exact:true}).click();await page.getByRole('button',{name:'待处理',exact:true}).click();await page.getByRole('searchbox').fill('很长的中文工单查询'.repeat(8));
await page.getByRole('heading',{name:'没有符合当前条件的服务记录'}).waitFor();assert.equal(await page.locator('.service-case').count(),0);assert(await page.getByText('0 条结果 · 商城 · 待处理').isVisible());
if(process.env.SCREENSHOT_DIR){await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/no-matches-${width}.png`});await page.evaluate(()=>document.documentElement.dataset.theme='dark');await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/no-matches-dark-${width}.png`});
// Composite translucent backgrounds through ancestors before checking rendered contrast.
const contrast=await page.locator('.service-types button.active').evaluate(el=>{
  const parse=s=>s.match(/[\d.]+/g).map(Number);
  const blend=(fg,bg)=>fg.slice(0,3).map((x,i)=>x*(fg[3]??1)+bg[i]*(1-(fg[3]??1)));
  const chain=[];for(let node=el;node;node=node.parentElement)chain.unshift(node);
  let bg=[255,255,255];for(const node of chain)bg=blend(parse(getComputedStyle(node).backgroundColor),bg);
  const fg=blend(parse(getComputedStyle(el).color),bg);
  const lum=rgb=>rgb.map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4}).reduce((sum,x,i)=>sum+x*[.2126,.7152,.0722][i],0);
  const a=lum(fg),b=lum(bg);return(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
});assert(contrast>=4.5,`Dark selected filter contrast: ${contrast}`);
await page.evaluate(()=>document.documentElement.dataset.theme='light')}
await page.getByRole('button',{name:'重置全部条件'}).click();assert.equal(await page.locator('.service-case').count(),3);assert.equal(await page.getByRole('searchbox').inputValue(),'');assert.equal(await page.evaluate(()=>document.activeElement.id),'service-search-input');
await page.getByRole('button',{name:'待处理',exact:true}).click();await page.getByRole('searchbox').fill('UNKNOWN');await page.getByRole('button',{name:'清除搜索'}).click();assert.equal(await page.locator('.service-case').count(),1);assert.equal(await page.getByRole('button',{name:'待处理',exact:true}).getAttribute('aria-pressed'),'true');await page.getByRole('button',{name:'重置全部条件'}).click();
assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
if(process.env.SCREENSHOT_DIR)await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/service-list-${width}.png`,fullPage:true});
failDetail=true;await page.locator('.service-case').first().click();await page.getByRole('button',{name:'重新读取'}).click();await page.getByRole('heading',{name:'报价待确认',exact:true}).waitFor();
assert.equal(await page.locator('.service-information').innerText().then(s=>s.includes('邮寄送修')),true);
assert.equal(await page.locator('.grand-total').innerText().then(s=>s.includes('189.00')),true);
assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
if(process.env.SCREENSHOT_DIR)await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/service-detail-${width}.png`,fullPage:true});
await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new Error('fixture denied')}}}));
await page.getByRole('button',{name:'复制工单号'}).click();assert(await page.locator('.service-information .copy-feedback').isVisible());
await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{}}}));await page.getByRole('button',{name:'复制工单号'}).click();assert(await page.getByText('工单号已复制',{exact:true}).isVisible());
await page.getByRole('button',{name:'下载报价单'}).click();assert(await page.getByRole('button',{name:'正在下载…'}).isDisabled());
await page.getByRole('button',{name:'重试下载报价单'}).waitFor();assert(await page.locator('#repair-quote .download-feedback').isVisible());assert.equal(pdfRequests,1);
await page.locator('.quote-heading').evaluate(el=>el.scrollIntoView({block:'start'}));
if(process.env.SCREENSHOT_DIR){await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/download-error-${width}.png`});await page.evaluate(()=>document.documentElement.dataset.theme='dark');await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/download-error-dark-${width}.png`});await page.evaluate(()=>document.documentElement.dataset.theme='light')}
const download=page.waitForEvent('download');await page.getByRole('button',{name:'重试下载报价单'}).focus();await page.keyboard.press('Enter');assert((await download).suggestedFilename().endsWith('.pdf'));assert.equal(pdfRequests,2);
assert(await page.getByText('已请求浏览器保存报价单，请在下载列表中查看。').isVisible());assert.equal(await page.locator('.download-feedback.failed').count(),0);

page.once('dialog',d=>d.dismiss());await page.getByRole('button',{name:'接受报价',exact:true}).click();assert.equal(decisions,0);
page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'接受报价',exact:true}).click();await page.getByRole('heading',{name:'报价已确认',exact:true}).waitFor();assert.equal(decisions,1);assert.equal(await page.getByRole('button',{name:'接受报价',exact:true}).count(),0);
await page.getByRole('button',{name:'返回服务列表'}).click();await page.locator('.service-case').first().waitFor();assert.equal(await page.locator('.service-case').count(),3);assert.equal(await page.evaluate(()=>document.activeElement?.getAttribute('data-work-key')),'repair:1');
await page.evaluate(()=>{document.documentElement.dataset.theme='dark'});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
// A direct link loads only the authorized detail endpoint, without needing a list match.
failDetail=true;await page.goto(`http://127.0.0.1:${server.address().port}/client/work-orders?repair=1`);
await page.getByRole('button',{name:'重新读取'}).click();await page.getByRole('heading',{name:'报价已确认',exact:true}).waitFor();
assert.equal(await page.locator('.service-case').count(),0);
const beforeRefresh=detailRequests;await page.getByRole('button',{name:'刷新维修进度'}).click();await page.getByRole('heading',{name:'报价已确认',exact:true}).waitFor();assert.equal(detailRequests,beforeRefresh+1);
await page.reload();await page.getByRole('heading',{name:'报价已确认',exact:true}).waitFor();
if(process.env.SCREENSHOT_DIR)await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/direct-repair-${width}.png`,fullPage:true});
await page.getByRole('button',{name:'返回服务列表'}).click();await page.locator('.service-case').first().waitFor();assert.equal(new URL(page.url()).search,'');assert.equal(await page.evaluate(()=>document.activeElement?.getAttribute('data-work-key')),'repair:1');
await page.goto(`http://127.0.0.1:${server.address().port}/client/work-orders?repair=999`);await page.getByText('未找到可访问的维修工单').waitFor();assert.equal(await page.locator('.service-case').count(),0);
if(process.env.SCREENSHOT_DIR){await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/direct-error-${width}.png`});await page.evaluate(()=>document.documentElement.dataset.theme='dark');await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/direct-error-dark-${width}.png`})}
await page.getByRole('button',{name:'返回服务列表'}).click();await page.locator('.service-case').first().waitFor();
holdDetail=true;const lateResponse=page.waitForResponse('**/api/client/repair/2');await page.goto(`http://127.0.0.1:${server.address().port}/client/work-orders?repair=2`);await page.getByText('正在加载详情…').waitFor();
await page.getByRole('button',{name:'返回服务列表'}).click();await page.locator('.service-case').first().waitFor();assert.equal(typeof releaseDetail,'function');releaseDetail();holdDetail=false;await lateResponse;await page.waitForTimeout(50);assert.equal(await page.locator('.service-case').count(),3);assert.equal(await page.locator('.work-detail').count(),0);
for(const value of ['abc','-1','9007199254740992','1&repair=2']){const before=detailRequests;await page.goto(`http://127.0.0.1:${server.address().port}/client/work-orders?repair=${value}`);await page.getByText('工单链接无效，请返回服务列表查找。').waitFor();assert.equal(detailRequests,before);await page.getByRole('button',{name:'返回服务列表'}).click();await page.locator('.service-case').first().waitFor()}
emptyAccount=true;await page.goto(`http://127.0.0.1:${server.address().port}/client/work-orders`);await page.getByRole('heading',{name:'当前账号还没有服务记录'}).waitFor();assert.equal(await page.getByRole('button',{name:'重置全部条件'}).count(),0);
assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
if(process.env.SCREENSHOT_DIR)await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/empty-account-${width}.png`});
await page.getByRole('link',{name:'申请维修',exact:true}).click();await page.waitForURL('**/client/repair/new');await page.locator('form').waitFor();
assert.deepEqual(errors,[]);console.log(`PASS ${width}px: list/detail retries, filters, search, overflow, download, decision cancel/accept, return, no JS errors`);await page.close();
}
}finally{await browser.close();server.close()}})().catch(e=>{console.error(e);server.close();process.exitCode=1});
