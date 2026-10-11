"""Optional private external test receipts. Never copy credentials or internal grants."""
import json
import os
from pathlib import Path
import shutil

def retain_jobs(app, label):
    reference = os.environ.get('GARAGE_TEST_RECEIPT_DIR')
    if not reference: return
    root = Path(reference)/label; root.mkdir(parents=True,exist_ok=True,mode=0o700)
    for job in app.jobs.list():
        target = root/job['id']; target.mkdir(mode=0o700,exist_ok=True)
        path = target/'public-job.json'; path.write_text(json.dumps(job,sort_keys=True,allow_nan=False)); path.chmod(0o600)
        source = app.jobs.directory(job['id'])
        for name in ('manifest.json','excitation.wav','capture.wav','evidence.json','guardian-exit.json','child.stdout','child.stderr'):
            if (source/name).is_file(): shutil.copy2(source/name,target/name)
        for path in source.glob('native*'):
            if path.is_file(): shutil.copy2(path,target/path.name)
