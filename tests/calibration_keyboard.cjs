// Focused real-Tab follow-up; isolated FILE acceptance only, no acoustic output.
const {chromium}=require('/private/tmp/opencode/garage-capture-env-U6wknV/browser-runner/node_modules/playwright');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
async function main(){
 const root=process.argv[2],out=process.argv[3];fs.mkdirSync(out,{mode:0o700});const state=JSON.parse(fs.readFileSync(path.join(root,'browser-state-v4/ready.json')));
 const browser=await chromium.launch({headless:true,executablePath:'/Users/zach/Library/Caches/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-mac-x64/chrome-headless-shell'});
 try{const page=await browser.newPage({viewport:{width:390,height:844}});await page.goto('http://127.0.0.1:18772');await page.locator('#token').fill(fs.readFileSync(path.join(root,'browser-state-v4/token'),'utf8'));await page.locator('#login-form button').click();await page.waitForFunction(()=>!document.querySelector('#correction-refresh').disabled);await page.locator('.sidebar nav a[href="#verify"]').click();
  await page.locator('#correction-before').selectOption(state.before);await page.waitForFunction(()=>!document.querySelector('#correction-prepare').disabled);await page.locator('#correction-after').selectOption(state.after);await page.waitForFunction(()=>!document.querySelector('#correction-after').disabled);
  await page.locator('#correction-prepare').click();await page.waitForFunction(()=>!document.querySelector('#correction-apply').disabled);await page.locator('#correction-apply').click();await page.waitForFunction(()=>!document.querySelector('#correction-rollback').disabled);
  await page.locator('#correction-verify').scrollIntoViewIfNeeded();await page.locator('#correction-verify').focus();await page.keyboard.press('Tab');
  const observed=await page.locator('#correction-rollback').evaluate(e=>{const r=e.getBoundingClientRect(),n=document.querySelector('.sidebar').getBoundingClientRect();return {focused:e===document.activeElement,focusVisible:e.matches(':focus-visible'),top:r.top,bottom:r.bottom,left:r.left,right:r.right,navBottom:n.bottom,viewport:{width:innerWidth,height:innerHeight}}});
  assert(observed.focused);assert(observed.focusVisible);assert(observed.top>=observed.navBottom&&observed.bottom<=844,JSON.stringify(observed));assert(observed.left>=0&&observed.right<=390);
  await page.screenshot({path:path.join(out,'keyboard-real-tab-390.png')});await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('#correction-state').textContent.includes('rolled back'));fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({...observed,rollback:'server readback rolled_back',source:'synthetic FILE fixture; no sound'},null,2));console.log(JSON.stringify(observed));
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e.stack);process.exitCode=1});
