// Actual headless browser; isolated HTTP + synthetic native backend, not phone.
const {chromium}=require('/private/tmp/opencode/garage-ux22-runner/node_modules/playwright');
const {execFileSync}=require('node:child_process');
const assert=require('node:assert/strict');
async function main(){
 const browser=await chromium.launch({headless:true,executablePath:'/Users/zach/Library/Caches/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-mac-x64/chrome-headless-shell'});
 try {
  for(const width of [390,1280]){
   const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();
   await page.goto('http://127.0.0.1:18798');
   await page.waitForFunction(()=>document.querySelector('#connection-label').textContent==='Locked');
   await page.locator('#token').fill('ISOLATED-BROWSER-FIXTURE-TOKEN');
   await page.locator('#login-form button').click();
   try {await page.waitForFunction(()=>document.querySelector('#speakers-code').textContent.length===12);} catch(e) {
    console.error('isolated panel diagnostic',await page.locator('#speakers-status').textContent(),await page.locator('#login-error').textContent());throw e;
   }
   await page.locator('#speakers-setup').click();
   await page.waitForFunction(()=>document.querySelector('#speakers-status').textContent.startsWith('Prepared'));
   assert.equal(await page.locator('#speakers-enable').isDisabled(),true);
   const code=await page.locator('#speakers-code').textContent();
   // This socket is owned by the isolated fixture UID, never production root.
   execFileSync('ssh',['-o','BatchMode=yes','zach@192.168.68.74',
    `cd ${process.env.GARAGE_BROWSER_SOURCE} && /home/zach/garage-audio/venv/bin/python -c 'from garage_audio.speaker_controller import request; from pathlib import Path; request({"cmd":"pair","code":"${code}"},Path("${process.env.GARAGE_BROWSER_FIXTURE}/pair.sock"))'`]);
   await page.waitForFunction(()=>!document.querySelector('#speakers-enable').disabled);
   await page.locator('#speakers-enable').click();
   await page.waitForFunction(()=>document.querySelector('#speakers-status').textContent.startsWith('Playback enabled'));
   await page.locator('#speakers-stop').click();
   await page.waitForFunction(()=>document.querySelector('#speakers-status').textContent.startsWith('Stopped'));
   await page.reload();
   await page.waitForFunction(()=>document.querySelector('#speakers-paired').textContent.startsWith('This browser is paired'));
   assert.equal(await page.locator('#speakers-enable').isDisabled(),true);
   // Logout from an active owned listening window must revoke BEFORE locking.
   await page.locator('#speakers-setup').click();
   await page.waitForFunction(()=>!document.querySelector('#speakers-enable').disabled);
   await page.locator('#speakers-enable').click();
   await page.waitForFunction(()=>document.querySelector('#speakers-status').textContent.startsWith('Playback enabled'));
   await page.locator('#sign-out').click();
   await page.waitForFunction(()=>document.querySelector('#connection-label').textContent==='Locked');
   assert.equal((await context.cookies()).some(c=>c.name==='garage_speakers_v1'),false);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   console.log('ISOLATED actual Chromium',width,'Setup → code → fixture pairing → Enable → Stop → reload → Enable → logout/revoke PASSED; no hardware');
   await context.close();
  }
 } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1});
