"""Supported existing-provider policy admission before transport registration."""
import asyncio,json,pathlib
import aiohttp
from music_assistant_client import MusicAssistantClient
async def main():
 async with aiohttp.ClientSession(trust_env=False) as s:
  async with MusicAssistantClient('http://music-assistant:8095',s,token=pathlib.Path('/auth-ma/token').read_text().strip()) as ma:
   entries=await ma.config.get_provider_config_entries('spotify_connect',instance_id='spotify_connect')
   mode=[e for e in entries if e.key=='volume_mode']
   if len(mode)!=1 or mode[0].hidden or not any(o.value=='spotify_authoritative' and not o.disabled for o in mode[0].options or ()):raise RuntimeError('selected Soloist reviewed policy not advertised')
   binding=await ma.config.get_provider_config_value('spotify_connect','connected_players')
   if binding!=['02:00:00:00:21:01']:raise RuntimeError('exact retained player binding required')
   await ma.config.save_provider_config('spotify_connect',{'volume_mode':'spotify_authoritative'},instance_id='spotify_connect')
   assert await ma.config.get_provider_config_value('spotify_connect','volume_mode')=='spotify_authoritative'
   print(json.dumps({'policy_admission':True,'sound_authority':False}))
try:asyncio.run(main())
except Exception as e:
 print(json.dumps({'safe_error_class':type(e).__name__,'safe_error_code':getattr(e,'error_code',None),'policy_admission':False}));raise SystemExit(1)
