#!/usr/bin/env python3
"""Owned host udhcpc hook: records lease only; never changes an active namespace address.

Operator lifecycle factory is garage_audio.audio_network.Carrier, not an HTTP helper.
"""
import json
import os
from pathlib import Path
import sys
import time

root = Path(os.environ['GARAGE_LEASE_RECEIPT'])
value = {'event': sys.argv[1], 'received': time.monotonic()}
if sys.argv[1] in ('bound', 'renew'):
    value.update(ip=os.environ['ip'], mask=os.environ['subnet'],
        gateway=os.environ.get('router', '').split()[0], seconds=int(os.environ['lease']),
        server=os.environ.get('serverid'))
    if value['seconds'] <= 0:
        raise ValueError('positive DHCP lease required')
text = json.dumps(value)
with (root/'lease-events.jsonl').open('a') as history:
    history.write(text+'\n')
temporary = root/'lease.next'
temporary.write_text(text)
os.replace(temporary, root/'lease.json')
