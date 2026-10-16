"""Actual adapter cancellation + native Policy/Camilla FILE; synthetic peer/receiver/PCM.

No production socket, MA account, protected provider PCM or ALSA device is used.
"""
import asyncio
import json
import time
import unittest
import socket
import importlib
from types import SimpleNamespace as NS
from connect_native_file import ConnectFile
from test_connect_adapter import adapter, authority, Client, Provider
runtime=importlib.import_module(adapter.__package__+'.connect_runtime')
from garage_audio.profile import compile_profile


class CancelFile(unittest.TestCase):
    def test_actual_adapter_cancel_zero_then_fresh_file(self):
        first=ConnectFile('test_native_connect_pause_resume_headroom');first.setUp()
        second=ConnectFile('test_native_connect_pause_resume_headroom');second.setUp()
        self.addCleanup(first.doCleanups);self.addCleanup(second.doCleanups)
        first.output=first.logs/'cancel-zero.packed24'
        first.policy.config_factory=lambda p:compile_profile(p,{'type':'Stdin','channels':2,'format':'F32_LE'},
            {'type':'File','channels':12,'format':'S24_3_LE','filename':str(first.output)})
        first.peer.realm='phone'
        def execute(stage,command,**fields):
            return stage.policy.execute(dict(cmd=command,generation=stage.policy.generation(),**fields),stage.peer)
        async def journey():
            client=Client();provider=Provider(client)
            daemon=NS(player_id=authority.TARGET,active_player_id=None,in_use_by_player=None,active_session_id=None)
            entered=asyncio.Event();release=asyncio.Event();calls=0;execute_second=None;queue=None
            a,b=socket.socketpair();a.setblocking(False);b.setblocking(False)
            ipc=runtime.NativeBridge.__new__(runtime.NativeBridge)
            ipc.reader,ipc.writer=await asyncio.open_unix_connection(sock=a,limit=4096)
            reader,writer=await asyncio.open_unix_connection(sock=b,limit=4096)
            ipc.socket=a;ipc.lock=asyncio.Lock();ipc.closed=False;ipc.failed_transport=False;ipc.denied=False
            ipc.armed=True;ipc.epoch=1;ipc.port=0;ipc.volume=lambda:27
            async def framed_native():
                command=json.loads(await reader.readline());assert command['cmd']=='connect-prepare'
                execute(first,'phone-start');execute(first,'initialize');execute(first,'phone-prepare')
                readback=execute(first,'status')
                assert readback['muted'] and readback['preparation_zero'] and readback['volume_db']==-60
                entered.set();await release.wait()
                writer.write(b'{"ok":true,"result":{"state":"prepared","generation":"old-zero"}}\n');await writer.drain()
                command=json.loads(await reader.readline());assert command['cmd']=='connect-pulse' and command['state']=='paused'
                # Synthetic gateway seam, actual Policy readback; no human/receiver attestation claim.
                status=execute(first,'status')
                assert status['muted'] and status['preparation_zero'] and status['volume_db']==-60
                writer.write(b'{"ok":true,"result":{"state":"prepared"}}\n');await writer.drain()
            server=asyncio.create_task(framed_native())
            class Bridge:
                async def attest(self,*args):return authority.ReceiverBinding('synthetic-file',(1,2,'fixture','sha'),1)
                async def prepare(self,token):
                    nonlocal calls,execute_second
                    calls+=1
                    if calls==1:
                        return await ipc.request('connect-prepare')
                    execute_second=second.open_connect()
                    return second.policy.generation()
                async def verify_preparation_pause(self,binding):
                    return await ipc.verify_preparation_pause(binding)
                def pause(self):pass # Actual first native graph already proven mute/all-zero.
                def revoke(self):first.policy.stop()
                async def pulse(self,*args):return True
            bridge=Bridge();value=adapter.ConnectAdapter(NS(_client=client),provider,daemon,bridge);value.install()
            async def normalized(event):
                if event.type=='playback_changed' and event.data.status=='paused' and queue:queue.cancel()
            listener=asyncio.create_task(client.listen_events(normalized));await asyncio.sleep(0)
            async def event(kind,**fields):
                await client.events.put(NS(type=kind,data=NS(**fields)));await asyncio.sleep(0);await asyncio.sleep(0)
            try:
                await event('auth_state',logged_in=True,is_active=True)
                await event('playback_state',status='paused',is_active=True)
                await event('playback_changed',status='playing',is_active=True)
                queue=asyncio.create_task(provider._deferred_play_media_fire(daemon));await entered.wait()
                await asyncio.sleep(.1)
                assert not any(any(x) for x in first.samples(0,first.output.stat().st_size))
                await event('playback_changed',status='paused',is_active=True);release.set()
                try:await queue
                except asyncio.CancelledError:pass
                else:raise AssertionError('adapter queue not cancelled')
                await server
                first.process.wait(timeout=3)
                assert not value.authority._closed and value.authority._token is None
                await event('playback_state',status='playing',is_active=True)
                await provider._deferred_play_media_fire(daemon);assert provider.queues==0 and calls==1
                await event('playback_changed',status='paused',is_active=True)
                await event('playback_changed',status='playing',is_active=True)
                await provider._deferred_play_media_fire(daemon);assert provider.queues==1 and calls==2
                execute_second('connect-state',state='playing');await asyncio.sleep(.8)
                samples=second.samples(0,second.output.stat().st_size)
                assert any(samples[2]) and any(samples[3])
                assert all(not any(samples[i]) for i in range(12) if i not in (2,3))
                assert max(map(abs,samples[2]))<=int(2**23*.25*10**(-6/20))+3
                result={'actual_adapter_queue_cancelled':True,'cancelled_native_process_exit':first.process.returncode,
                    'first_graph_zero':True,'snapshot_reopen':False,'fresh_delta_second_queue':True,
                    'new_native_generation':True,'out3_4_minus6dB':True,'ten_spares_zero':True,
                    'cancelled_reply_drained_before_verified_pause':True,
                    'synthetic_peer_receiver_pcm':True,'hardware':False,'MA_Soloist_runtime':False}
                (second.logs/'adapter-cancel-readback.json').write_text(json.dumps(result,indent=2))
                print('PASS actual_adapter_native_policy_Camilla_FILE_cancel_zero_fresh_delta',json.dumps(result),flush=True)
            finally:
                await value.close();await asyncio.gather(listener,return_exceptions=True)
                for stream in (ipc.writer,writer):
                    stream.close();await stream.wait_closed()
                if not server.done():server.cancel()
                await asyncio.gather(server,return_exceptions=True)
        asyncio.run(journey())


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([
        CancelFile('test_actual_adapter_cancel_zero_then_fresh_file')]))
    raise SystemExit(0 if result.wasSuccessful() else 1)
