"""Real HTTP/Application + FILE worker, explicitly SYNTHETIC paired UI data.

In-process public runtime bridge; no devices, sound, physical mic or live authority.
Only an isolated loopback-published container should run this browser fixture.
"""
import copy
import json
import os
from pathlib import Path
import sys
from garage_audio.application import Application
from garage_audio.audio_container import Worker
from garage_audio.webserver import Server
from garage_audio import audio_public,audio_config
from measurement_workflow import fixture


def main():
    root=Path(sys.argv[1]);root.mkdir(parents=True,mode=0o700,exist_ok=True)
    app=Application(root/'app');worker=Worker(root/'worker',root/'evidence')
    def runtime(op,args=None):
        if op in ('audio.status','audio.stop','audio.pause'):
            if op!='audio.status':worker.halt('synthetic_browser_stop')
            return worker.status()
        return worker.configurations.execute(op,args or {})
    audio_public.request=runtime  # Isolated fixture bridge, never production.
    def op(operation,**args):
        result=app.execute(operation,args)
        if not result['ok']:raise RuntimeError(result['error']['code'])
        return result['result']
    before=op('calibration.plan',name='SYNTHETIC · Before seat',channels=['left'])['id']
    after=op('calibration.plan',name='SYNTHETIC · After seat',channels=['left'])['id']
    op('measurements.import',session_id=before,**fixture())
    response=fixture();response['text']='\n'.join(f'{20*50**(i/240):.8f} 70' for i in range(241));op('measurements.import',session_id=after,**response)
    base={'binding':worker.configurations.binding,'configuration':copy.deepcopy(audio_config.DEFAULT),'revision':0,'generation':worker.generation,'compiler_revision':audio_config.COMPILER}
    def bind(sid,binding):
        session=app.read('sessions',sid)
        session['files'][0]['capture_provenance']={'fixture':'SYNTHETIC browser data, not captured PCM','hardware_sha256':'d'*64,
            'correction_render':{'binding':binding,'native_exit_status':0,'unused_outputs_zero':True,'input_sha256':'b'*64,'emitted_sha256':'a'*64,'binary_sha256':'c'*64}}
        app.save('sessions',session)
    bind(before,base);op('calibration.analyze',id=before);proposal=op('calibration.propose',id=before)['proposal']
    corrected=copy.deepcopy(base);corrected['revision']=1;corrected['configuration']['correction']={c:proposal['channels'].get(c,{}).get('peq',[]) for c in ('left','right','sub')};bind(after,corrected)
    # Completed synthetic hardware-adapter fixture checks truthful UI labeling only.
    jid='f'*32;directory=app.jobs.directory(jid);directory.mkdir(mode=0o700)
    app.jobs._save({'id':jid,'session_id':before,'state':'completed','adapter':'hardware','source':'synthetic','hardware_enabled':False,'sound_disabled':True,
        'manifest':{'version':4,'realm':'SYNTHETIC','position':'seat','channel':'left','hardware_admission':{'policy':{'outputs':{'left':3}}},'budgets':{'total_seconds':30},'policy':{'output_peak_dbfs':-60},'correction_render':{'binding':base}},
        'manifest_sha256':'a'*64,'approval':{'approved':False},'history':[],'evidence':{'source':'synthetic','supported_magnitude':True,'capture_sha256':'b'*64,'checks':{'synthetic_fixture_only':True},'warnings':['Synthetic browser fixture, not a microphone capture']}})
    token=root/'token';token.write_text(os.urandom(32).hex());token.chmod(0o600)
    server=Server(('0.0.0.0',8098),app,token.read_text(),Path(__file__).resolve().parents[1]/'web',['127.0.0.1:18772','localhost:18772'])
    (root/'ready.json').write_text(json.dumps({'before':before,'after':after,'token_ref':str(token),'source':'synthetic','port':18772}))
    print('SYNTHETIC browser fixture ready',flush=True)
    try:server.serve_forever()
    finally:server.server_close();worker.closed.set();worker.halt('fixture_shutdown')


if __name__=='__main__':main()
