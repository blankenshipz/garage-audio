// Isolated real browser/HTTP; synthetic native. No pair, Enable or actual music.
const {chromium}=require('/private/tmp/opencode/garage-ux22-runner/node_modules/playwright');
const assert=require('node:assert/strict');
async function main(){
 const browser=await chromium.launch({headless:true,executablePath:'/Users/zach/Library/Caches/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-mac-x64/chrome-headless-shell'});
 try{for(const width of [390,1280]){
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();
  await page.goto('http://192.168.68.74:18798');await page.waitForFunction(()=>!document.querySelector('#speakers-stop').disabled);
  assert.equal(await page.locator('#login-panel').isVisible(),false);
  const card=page.locator('[aria-labelledby="speakers-title"]');
  assert.equal(await card.getByRole('link',{name:'Open Music Assistant · music, Play and volume'}).getAttribute('href'),'http://192.168.68.74:8095');
  assert.equal(await page.locator('#speakers-code').count(),0);
  assert.equal(await card.getByText('Set up speakers (muted)',{exact:true}).count(),0);
  assert.equal(await card.getByText('Enable playback',{exact:true}).count(),0);
  await card.locator('summary').click();assert.equal(await page.locator('#speakers-enable').isDisabled(),true);
  await page.locator('#speakers-setup').click();await page.waitForFunction(()=>document.querySelector('#speakers-status').textContent.startsWith('Speaker check prepared'));
  await page.locator('#speakers-stop').click();await page.waitForFunction(()=>document.querySelector('#speakers-status').textContent.startsWith('Speaker output stopped'));
  await page.reload();await page.waitForFunction(()=>!document.querySelector('#speakers-stop').disabled);
  assert.equal(await page.locator('#speakers-code').count(),0);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  console.log('ISOLATED Chromium'+width+' music-first/no codes/jargon; honest source/player/output separation; silentcheck→Stop→reload; no pair/Enable/audio');await context.close();
 }}finally{await browser.close();}
}
main().catch(e=>{console.error(e.name,e.message);process.exitCode=1});
