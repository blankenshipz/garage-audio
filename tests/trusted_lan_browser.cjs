// Actual isolated LAN HTTP/UI; synthetic native; no token read, pair or Enable.
const {chromium}=require('/private/tmp/opencode/garage-ux22-runner/node_modules/playwright');
const assert=require('node:assert/strict');
async function main(){
 const browser=await chromium.launch({headless:true,executablePath:'/Users/zach/Library/Caches/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-mac-x64/chrome-headless-shell'});
 try{for(const width of [390,1280]){
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();
  await page.goto('http://192.168.68.74:18798');
  await page.waitForFunction(()=>!document.querySelector('#speakers-setup').disabled);
  assert.equal(await page.locator('#login-panel').isVisible(),false);assert.equal(await page.locator('#sign-out').isVisible(),false);
  assert.equal(await page.locator('#connection-label').textContent(),'Home LAN');
  assert.equal(await page.locator('#speakers-stop').isDisabled(),false);
  assert.equal(await page.locator('#speakers-enable').isDisabled(),true);
  await page.reload();await page.waitForFunction(()=>!document.querySelector('#speakers-setup').disabled);
  await page.locator('#speakers-setup').click();await page.waitForFunction(()=>document.querySelector('#speakers-status').textContent.startsWith('Prepared'));
  assert.equal(await page.locator('#speakers-enable').isDisabled(),true);
  const denial=await page.evaluate(async()=>{const s=(await (await fetch('/api/session')).json()).result;return (await fetch('/api/speakers/enable',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf},body:JSON.stringify({action:'NOT-A-HUMAN-ACTION'})})).status});
  assert.equal(denial,409);
  await page.locator('#speakers-stop').click();await page.waitForFunction(()=>document.querySelector('#speakers-status').textContent.startsWith('Stopped'));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  console.log('ISOLATED actualLAN Chromium'+width+' fresh/reload noauthUI→syntheticSetup→Stop; unpairedEnable409/no pair/no token/no audio');
  await context.close();
 }}finally{await browser.close();}
}
main().catch(e=>{console.error(e.name,e.message);process.exitCode=1});
