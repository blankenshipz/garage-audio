"""Retained per-scenario stdout/stderr/exit for actual isolated normal runtime."""
import json,os,subprocess,sys,time,uuid
from pathlib import Path
root=Path(__file__).resolve().parents[1]
receipt=root/'state'/'normal-fault-receipts';receipt.mkdir(mode=0o700,exist_ok=False)
results={}
run_id=uuid.uuid4().hex[:8]
for mode in sys.argv[1:] or ['integration','logout','permission','expired','worker_death','ref_drift','device_drift','deadline','watchdog_stall','restart_only']:
    with (receipt/(mode+'.stdout')).open('wb') as out,(receipt/(mode+'.stderr')).open('wb') as error:
        start=time.monotonic()
        reply=subprocess.run([sys.executable,str(root/'tests/normal_transport_file.py'),'garage-normal-file-fault-'+mode.replace('_','-')+'-r17-'+run_id,mode],stdout=out,stderr=error)
    results[mode]={'exit':reply.returncode,'duration':time.monotonic()-start};(receipt/(mode+'.exit')).write_text(str(reply.returncode)+'\n')
    print(mode,reply.returncode,round(results[mode]['duration'],2),flush=True)
    if reply.returncode:break
(receipt/'results.json').write_text(json.dumps(results,indent=2))
sys.exit(1 if any(v['exit'] for v in results.values()) else 0)
