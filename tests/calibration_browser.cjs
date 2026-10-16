// Actual Chromium rendering against real isolated HTTP/Application + FILE worker.
// Paired response/provenance data are SYNTHETIC; no hardware/acoustic proof.
const {chromium}=require('/private/tmp/opencode/garage-capture-env-U6wknV/browser-runner/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
async function main(){
 const root=process.argv[2],proof=process.argv[3],state=process.argv[4]||'browser-state';fs.mkdirSync(proof,{mode:0o700});
 const ready=JSON.parse(fs.readFileSync(path.join(root,state,'ready.json'),'utf8'));
 const browser=await chromium.launch({headless:true,executablePath:'/Users/zach/Library/Caches/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-mac-x64/chrome-headless-shell'});
 const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage(),errors=[],geometry=[];
 page.on('pageerror',e=>errors.push(e.message));
 async function screenshot(name){await page.locator('#calibration-lifecycle').screenshot({path:path.join(proof,name+'.png')});}
 async function fits(width){
  const result=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,controls:[...document.querySelectorAll('#calibration-lifecycle button,#calibration-lifecycle select')].filter(e=>e.getClientRects().length).map(e=>{const r=e.getBoundingClientRect();return {id:e.id,x:r.x,right:r.right,height:r.height};})}));
  assert(result.scrollWidth<=width,JSON.stringify(result));for(const box of result.controls){assert(box.x>=0&&box.right<=width+.1,JSON.stringify(box));assert(box.height>=44,JSON.stringify(box));}geometry.push(result);
 }
 try{
  await page.goto('http://127.0.0.1:18772/');await page.locator('#login-form').waitFor();
  await page.locator('#token').fill(fs.readFileSync(path.join(root,state,'token'),'utf8'));await page.locator('#login-form button').click();
  await page.waitForFunction(()=>!document.querySelector('#correction-refresh').disabled);
  const nav=page.locator('.sidebar nav a[href="#verify"]');await nav.focus();await page.keyboard.press('Enter');
  await page.locator('#correction-before').selectOption(ready.before);await page.waitForFunction(()=>!document.querySelector('#correction-prepare').disabled);
  await page.locator('#correction-after').selectOption(ready.after);await page.waitForFunction(()=>!document.querySelector('#correction-after').disabled);
  await page.locator('#correction-prepare').focus();assert(await page.locator('#correction-prepare').evaluate(e=>e===document.activeElement));await page.keyboard.press('Enter');
  await page.waitForFunction(()=>!document.querySelector('#correction-apply').disabled);
  await page.locator('#correction-apply').focus();await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.querySelector('#correction-state').textContent.includes('accepted for next play'));
  assert.match(await page.locator('#correction-state').innerText(),/SYNTHETIC/);await fits(1280);await screenshot('accepted-1280');
  await page.locator('#correction-verify').click();await page.waitForFunction(()=>document.querySelector('#correction-report').textContent.includes('Paired magnitude report'));
  assert.match(await page.locator('#correction-report').innerText(),/No acoustic improvement verified/);await screenshot('report-1280');
  await page.setViewportSize({width:390,height:844});await fits(390);await screenshot('report-390');
  await page.locator('#correction-rollback').focus();assert(await page.locator('#correction-rollback').evaluate(e=>e===document.activeElement));await page.screenshot({path:path.join(proof,'keyboard-rollback-390.png')});await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.querySelector('#correction-state').textContent.includes('rolled back'));
  await page.reload();await page.waitForFunction(()=>document.querySelector('#correction-state').textContent.includes('rolled back'));
  assert.equal(await page.locator('#correction-before').inputValue(),ready.before);assert.equal(await page.locator('#correction-after').inputValue(),ready.after);assert(await page.locator('#correction-apply').isDisabled());await fits(390);await screenshot('returned-390');
  await nav.focus();await page.keyboard.press('ArrowDown');assert.equal(await page.locator('.sidebar nav a[href="#advanced"]').getAttribute('aria-current'),'step');await page.keyboard.press('ArrowUp');assert.equal(await nav.getAttribute('aria-current'),'step');
  await page.locator('.sidebar nav a[href="#calibration"]').click();await page.locator('#file-only-experiments').evaluate(e=>e.open=true);await page.locator('#job-history-details').evaluate(e=>e.open=true);
  await page.locator('#job-history button').first().click();await page.waitForFunction(()=>document.querySelector('#job-activity').textContent.includes('Hardware acquisition'));
  assert.match(await page.locator('#job-activity').innerText(),/SYNTHETIC/);assert(await page.getByRole('button',{name:'Execute approved hardware capture',exact:true}).isDisabled());assert.equal(await page.locator('#human-login-form').count(),0);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.locator('#jobs').screenshot({path:path.join(proof,'synthetic-hardware-job-390.png')});
  await nav.click();await page.locator('#correction-acquisition > summary').click();await page.locator('#correction-capture').selectOption('f'.repeat(32));assert(await page.locator('#correction-import').isDisabled());
  assert.deepEqual(errors,[]);const result={source:'synthetic browser fixture',backend:'actual HTTP/Application with in-process FILE worker and real validator',journey:'select before/after -> keyboard prepare/apply -> paired report -> narrow keyboard rollback -> reload/selected persistence -> nav -> adapter-specific job evidence',widths:[1280,390],geometry,pageErrors:errors,hardwareActions:false,acousticProof:false};
  fs.writeFileSync(path.join(proof,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(e){await page.screenshot({path:path.join(proof,'failure.png'),fullPage:true});fs.writeFileSync(path.join(proof,'failure.json'),JSON.stringify({message:e.message,stack:e.stack,pageErrors:errors,url:page.url()},null,2));throw e;}
 finally{await context.close();await browser.close();}
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
