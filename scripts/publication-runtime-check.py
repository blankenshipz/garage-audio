#!/usr/bin/env python3
"""Installed Python 3.13 smoke check: imports, assets and quiet plan persistence.

No listener, hardware job, model call, playback or external credential is used.
"""
import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-layout', type=Path,
                        help='explicit immutable image app root; omit for installed wheel')
    args = parser.parse_args()
    if sys.version_info < (3, 13):
        raise RuntimeError('runtime acceptance requires Python 3.13+')
    modules = ('garage_audio.application', 'garage_audio.webserver',
               'garage_audio.mcp_server', 'garage_audio.measurement_hardware',
               'garage_audio.calibration_lifecycle')
    loaded = {name:importlib.import_module(name) for name in modules}
    package = Path(loaded['garage_audio.application'].__file__).resolve().parent
    if args.source_layout:
        root = args.source_layout.resolve()
        if package != root/'garage_audio':
            raise RuntimeError('image runtime is not the declared immutable source layout')
        assets = root
    else:
        checkout = Path(__file__).resolve().parents[1]
        if package == checkout/'garage_audio':
            raise RuntimeError('wheel smoke check accidentally imported checkout source')
        assets = Path(sys.prefix)/'share/garage-audio'
    for name in ('web/index.html', 'web/favicon.svg', 'profiles/uncalibrated.json'):
        if not (assets/name).is_file():
            raise RuntimeError(f'installed asset missing: {name}')
    def no_broker(*unused, **kwargs):
        raise AssertionError('quiet package smoke must not contact hardware broker')
    Application = loaded['garage_audio.application'].Application
    with tempfile.TemporaryDirectory(prefix='garage-package-smoke-') as temporary:
        state = Path(temporary)/'state'
        app = Application(state, broker=no_broker)
        planned = app.execute('calibration.plan', {'name':'package smoke quiet plan'})
        if not planned['ok'] or not planned['result']['sound_disabled']:
            raise RuntimeError('quiet plan creation failed')
        identifier = planned['result']['id']
        reopened = Application(state, broker=no_broker)
        read = reopened.execute('calibration.get', {'id':identifier})
        if not read['ok'] or read['result']['name'] != 'package smoke quiet plan':
            raise RuntimeError('quiet plan did not survive application reopen')
        cancelled = reopened.execute('calibration.cancel', {'id':identifier})
        if not cancelled['ok'] or cancelled['result']['state'] != 'cancelled':
            raise RuntimeError('quiet plan cancellation failed')
    print(json.dumps({'python':sys.version.split()[0], 'imports':list(loaded),
                      'assets':True, 'quiet_plan_reopen_cancel':True,
                      'hardware_opened':False, 'listener_started':False}))


if __name__ == '__main__':
    main()
