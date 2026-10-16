"""Host runner retaining all isolated container logs outside shipped inputs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

project = sys.argv[1]
receipt = Path(sys.argv[2])
receipt.mkdir(mode=0o700, parents=True, exist_ok=True)
if not project.startswith('gaudio-m2-'):
    raise RuntimeError('isolated project prefix required')
compose = ['docker', 'compose', '-p', project, '-f', 'compose.audio.yaml', '-f', 'tests/compose.audio-file.yaml']
if len(sys.argv) > 3:
    if sys.argv[3] != '--source-bound':
        raise RuntimeError('only fixed isolated source overlay allowed')
    compose += ['-f', 'tests/compose.audio-source.yaml']
services = ['music-assistant', 'audio-transport', 'audio-device']
def run(argv, name, check=True):
    with open(receipt/(name+'.stdout'), 'wb') as stdout, open(receipt/(name+'.stderr'), 'wb') as stderr:
        result = subprocess.run(argv, stdout=stdout, stderr=stderr, timeout=180)
    (receipt/(name+'.exit')).write_text(str(result.returncode)+'\n')
    if check and result.returncode:
        raise RuntimeError(name+' failed; retained '+str(receipt))
    return result.returncode
try:
    run(compose+['up', '-d']+services, 'start')
    time.sleep(3)
    run(compose+['exec', '-T', '--user', '1000:1003', 'audio-device', 'python', '-m',
        'garage_audio.audio_container', '--status'], 'startup-status')
    run(compose+['exec', '-T', 'audio-transport', 'python', '-c',
        "import runpy; runpy.run_path('/fixtures/audio_container_journey.py',run_name='__main__')"], 'journey')
    run(compose+['restart', 'audio-device'], 'worker-restart')
    time.sleep(1)
    run(compose+['exec', '-T', '--user', '1000:1003', 'audio-device', 'python', '-m',
        'garage_audio.audio_container', '--status'], 'restart-status')
    state = json.loads((receipt/'restart-status.stdout').read_text())
    assert state['state'] == 'off' and not state['owned_children']
    print('PASS restart_noautoplay_generation_reset')
    run(compose+['exec', '-T', '--user', '1000:1003', 'audio-device', 'python', '-c',
        "from garage_audio.audio_container import request; "
        "from pathlib import Path; "
        "assert request(Path('/run/garage-audio/public/control.sock'),{'operation':'audio.stop'})['state']=='off'"],
        'public-stop')
    print('PASS public_silence_only_usable')
    run(compose+['exec', '-T', '--user', '1000:1003', 'audio-device', 'python', '-c',
        "from garage_audio.audio_container import request; from pathlib import Path; "
        "\nfor path,payload in [('/run/garage-audio/public/control.sock',{'operation':'audio.play'}),"
        "('/run/garage-audio/events/event.sock',{'kind':'delta'})]:"
        "\n try: request(Path(path),payload)"
        "\n except RuntimeError: pass"
        "\n else: raise AssertionError('public actor elevated')"], 'public-denial')
    print('PASS public_play_and_event_elevation_denied')
finally:
    run(compose+['logs', '--no-color'], 'containers', check=False)
    # Explicit fixture shutdown, not cleanup: retain containers, volumes and evidence.
    run(compose+['stop', '-t', '3']+services, 'shutdown', check=False)
    print('Isolated fixture stopped; evidence and stopped containers/volumes retained: '+str(receipt))
