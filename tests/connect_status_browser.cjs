// Actual candidate DOM/CSS/Chromium geometry with synthetic status, no API/music.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('/private/tmp/opencode/garage-ux22-runner/node_modules/playwright');
const root=path.resolve(__dirname,'..');
const receipt=process.env.GARAGE_CONNECT_UI_RECEIPT;
if(!receipt)throw Error('private receipt directory required');fs.mkdirSync(receipt,{recursive:true,mode:0o700});
async function main(){
 const browser=await chromium.launch({headless:true,executablePath:'/Users/zach/Library/Caches/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-mac-x64/chrome-headless-shell'});
 const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<script[^>]*>[\s\S]*?<\/script>/g,'').replace('<link rel="stylesheet" href="/assets/style.css">','<style>'+fs.readFileSync(path.join(root,'web/style.css'),'utf8')+'</style>');
 const script=fs.readFileSync(path.join(root,'web/speakers.js'),'utf8').replace('export class SpeakersPanel','window.SpeakersPanel = class SpeakersPanel');
 const results=[];
 try{
  for(const width of [390,1280]){
   const page=await browser.newPage({viewport:{width,height:900}});await page.setContent(html);await page.addScriptTag({content:script});
   await page.evaluate(()=>{window.panel=new window.SpeakersPanel({request(){throw Error('render-only fixture cannot control anything');}});window.panel.csrf='SYNTHETIC-RENDER-ONLY';});
   for(const state of ['disabled','waiting_for_spotify_play','playing','paused','stopped']){
    await page.evaluate(state=>{const active=['playing','paused'].includes(state);window.panel.state={state:active?'listening':'stopped',mode:active?'spotify_connect':'off',transport:active?state:null,ma_volume_percent:27,spotify_connect:{state,armed:state!=='disabled',receiver_verified:true},music:{spotify_connect_configured:true},player_registered:true};window.panel.render();},state);
    const copy=await page.locator('#speakers-human-status').textContent();assert(!copy.includes('pressing Start listening'));
    if(state==='disabled')assert(copy.includes('Not ready for playback'));
    else assert(copy.includes('No MA session, code or periodic approval'));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    const box=await page.locator('[aria-labelledby="speakers-title"]').boundingBox();assert(box.x>=0&&box.x+box.width<=width+1);
    await page.locator('[aria-labelledby="speakers-title"]').screenshot({path:path.join(receipt,`${width}-${state}.png`)});
    results.push({width,state,box,syntheticStatus:true,actualRenderedDOM:true});
   }
   // Keyboard expansion remains optional diagnostics, not a source grant.
   await page.locator('[aria-labelledby="speakers-title"] summary').focus();await page.keyboard.press('Enter');
   assert.equal(await page.locator('[aria-labelledby="speakers-title"] details').getAttribute('open'),'');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.locator('[aria-labelledby="speakers-title"]').screenshot({path:path.join(receipt,`${width}-keyboard-diagnostics.png`)});
   await page.locator('nav a[href="#check"]').click();assert.equal(await page.evaluate(()=>location.hash),'#check');
   await page.close();
  }
  fs.writeFileSync(path.join(receipt,'geometry.json'),JSON.stringify({results,physicalPhone:false,deployed:false,sourceEvents:false},null,2));
  console.log('PASS candidate Chromium390/1280 disabled/waiting/playing/paused/stopped + keyboard diagnostics/nav geometry; synthetic status, no API or music');
 }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1});
