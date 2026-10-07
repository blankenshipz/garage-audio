# Real isolated native runtime proof

No mock server, simulator or substitute player was used in the native test. Official Ubuntu package binaries/libraries were downloaded with `apt download` into `/home/zach/garage-audio/tools/native/debs`, extracted with `dpkg-deb -x` into `tools/native/root`—**not installed into the host**, no sudo, no package-cache update, no root Docker workaround. Observed versions:

- pipewire/pipewire-bin/pipewire-alsa/libpipewire/libspa `1.6.2-1ubuntu1.2`
- wireplumber/libwireplumber `0.5.13-1ubuntu1`
- squeezelite `2.0.0-1541+git20250609.72e1fd8-1build1`

Shared codec dependencies were resolved from the host's existing apt metadata. Exact downloaded archive filenames/hash inventory is retained privately in project `tools/native/packages.sha256`; these are ordinary package hashes, not credentials. Application packages are pinned; distribution dependency closure is an observed snapshot, not a claimed immutable repository snapshot. Production administrator script pins the main package versions; startup version guard refuses unverified PW/WP/Squeezelite drift instead of silently substituting a backend.

To repeat with the existing verified user-local extraction:

```sh
cd /home/zach/garage-audio
source scripts/native-test-env
scripts/check-native-versions
PYTHONPATH=. venv/bin/python tests/native.py
```

Environment redirects library/module/plugin/config paths, dedicated socket/runtime, **policy state and cache** into the project. Test checks there is exactly one Audio/Sink `garage_music` at48000 and no ALSA nodes; all physical discovery monitors are disabled. Squeezelite opens only the explicit `garage_music` ALSA plugin and connects only to127.0.0.1; authenticated MA provider is not configured, so its actual MA discovery/queue/decoded playback is not claimed. Squeezelite initial plugin44100 clock is resampled by PW;48000-48000 is the advertised rate range, actual MA handshake pending.

Synthetic finite stereo PCM is played into this sink using real pw-play, captured from its explicitly fixed monitor with real pw-record, processed by pinned real CamillaDSP stdin→20-channel **FILE**. L/R/sub nonzero and17 unused channels exact zeros asserted. No DSP ALSA backend is instantiated in this test; unmute affects only the guarded file backend. Missing explicit monitor target rejects without alternate sink; real daemon restart recreates sink. Processes are owned/tracked/terminated in test finally blocks; no test services persist at SSH logout. PW/WP run without RTKit/realtime privileges, producing warnings; this does not establish sustained underrun performance.

This is local executable synthetic-PCM evidence on the real server components, not live MA/hardware/acoustic acceptance. Root user/system units, DAC permissions/clock drift, attached channels, USB/reboot faults and sustained playback remain unverified. See evidence scope-correction note about initial default WP state before state isolation was added.
