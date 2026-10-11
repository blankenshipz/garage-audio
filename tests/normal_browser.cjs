// Real rendered MA UI, isolated dummy login via private reference. Never production.
const {chromium}=require('/private/tmp/opencode/garage-ux22-runner/node_modules/playwright');
const {execFileSync,spawn}=require('node:child_process');
const crypto=require('node:crypto');
const fs=require('node:fs');
const assert=require('node:assert/strict');
async function main(){
 const source=process.argv[2], name=process.argv[4]||'garage-normal-file-browser-r17';
 assert(/^garage-normal-file-browser-r17(?:-v[0-9]+)?$/.test(name));
 assert(/^\/root\/garage-normal-file[0-9]+-r17\.[A-Za-z0-9]+\/source$/.test(source));
 const dir=source+'/state/case-'+crypto.createHash('sha256').update(name).digest('hex').slice(0,8);
 const ssh=(command)=>execFileSync('ssh',['-o','BatchMode=yes','zach@192.168.68.74',command],{encoding:'utf8'});
 const ip=JSON.parse(ssh('docker inspect '+name))[0].NetworkSettings.Networks['garage-ma-context-r16'].IPAddress;
 const credentials=JSON.parse(ssh('sudo -n cat '+dir+'/ma/browser-login.json'));
 const tunnel=spawn('ssh',['-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-N','-L','127.0.0.1:18895:'+ip+':8095','zach@192.168.68.74'],{stdio:'ignore'});
 const browser=await chromium.launch({headless:true,executablePath:'/Users/zach/Library/Caches/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-mac-x64/chrome-headless-shell'});
 try{
  const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage();
  await new Promise(r=>setTimeout(r,1000));await page.goto('http://127.0.0.1:18895');
  await page.locator('input').first().waitFor();
  await page.locator('input').nth(0).fill(credentials.username);
  await page.locator('input[type=password]').fill(credentials.password);
  await page.getByRole('button',{name:/sign in|log in|login/i}).click();
  await page.waitForTimeout(2000);
  console.log('UI_NAV',page.url(),(await page.locator('body').innerText()).slice(0,1200));
  await page.screenshot({path:process.argv[3]+'/ma-initial-390.png'});
  // Route is inspected from actual UI; no consent action is invoked here.
  await page.mouse.click(53,796);await page.waitForTimeout(300);
  const settings=page.getByRole('button',{name:/settings/i});
  if(await settings.count())await settings.first().click();
  else await page.getByRole('link',{name:/settings/i}).first().click();
  await page.waitForTimeout(1000);
  console.log('UI_SETTINGS',(await page.locator('body').innerText()).slice(0,1500));
  await page.screenshot({path:process.argv[3]+'/ma-settings-390.png'});
  await page.getByText('Plugins',{exact:true}).click();await page.waitForTimeout(500);
  console.log('UI_PLUGINS',(await page.locator('body').innerText()).slice(0,1800));
  await page.getByText('Garage Speakers control',{exact:true}).first().click();await page.waitForTimeout(500);
  console.log('UI_OPTIONS',(await page.locator('body').innerText()).slice(0,2400));
  await page.screenshot({path:process.argv[3]+'/ma-options-390.png'});
  const start=page.getByRole('button',{name:'Start listening',exact:true});await start.scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  const box=await start.boundingBox();assert(box.x>=0&&box.x+box.width<=390);
  await page.getByRole('button',{name:'4 hours',exact:true}).click();await page.waitForTimeout(700);
  assert(await page.getByText(/approve 4 hours on OUT3\/4 only/).count());
  await page.getByRole('button',{name:'Ceiling -40 dB',exact:true}).click();await page.waitForTimeout(700);
  assert(await page.getByText(/native ceiling -40 dB/).count());
  await start.scrollIntoViewIfNeeded();await start.focus();assert(await start.evaluate(e=>e===document.activeElement));
  await page.screenshot({path:process.argv[3]+'/ma-consent-selected-390.png'});
  // Browser-only fixture stays no physical output; do not approve even FILE here.
  await page.reload();await page.waitForTimeout(700);
  assert(await page.getByRole('button',{name:'Start listening',exact:true}).count());
  console.log('PASS actual Chromium390 MA login→Settings→Plugins→control; short unclipped Start, selected4h/-40 scope updates, keyboard focus, reload; no production login/consent, physicalphone unrun');
 }finally{await browser.close();tunnel.kill();}
}
main().catch(e=>{console.error(e.name,String(e.message).slice(0,500));process.exitCode=1});
