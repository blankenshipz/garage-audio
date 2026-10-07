# Phased evidence (self-reported, 2026-10-06/07)

## Deployed / executable tested

- New git project `/Users/zach/Code/garage-audio`, staged `/home/zach/garage-audio` on verified garage-server.
- MA 2.10.5 image digest `sha256:28023f8c0d96ca2496391f3218a6d70d7ef3ba84db846b3a3f3cd9ce7ef8aedc`, user1000, caps dropped, nonprivileged, loopback8095/8097/3483, persistent state, unless-stopped restart policy. HTTP302 on live UI endpoint (not authenticated UI journey proof). Official client reached server schema65 and rejected missing authentication token as expected.
- CamillaDSP4.1.3 official release archive hash `55f5ec2ed80fcc79a543672f9f89ace4557d290d80584ef31ee0442111bd0b11`; executable prints4.1.3/05e9cfc, websocket+RawFile/WavFile/Stdin/SignalGenerator/ALSA capture, File/Stdout/ALSA playback. Native PipeWire backend **not used**; native PW monitor bridge feeds Stdin.
- Host lacks libasound; extracted official Ubuntu `libasound2t64 1.2.15.3-1ubuntu1.1` only under project tools, hash `96e20cf5c9095ef54ba46fccffea5c01cf2de17a1012c5683a67e43504a6d726`. No host package installation or sudo bypass.
- Seven real-binary executable tests: LR4 -6dB at80Hz/highpass/lowpass attenuation; mono0.5L+0.5R cancellation; master -20 vs -60 ratio0.01 and mute exact zeros across all branches; all17 unused channels exact zeros; channel gain/mute/polarity/delay480samples at10ms; sub trim; PEQ cut/bypass; steady native ceiling; official-client stdin/null profile reload/readback/persistence/known-good/invalid-profile rollback; two startup cycles muted/-60; profile/REW validators.
- ALSA hardware config **schema-only** checked via `camilladsp -c` without device opens. `scripts/run-dsp` without commissioned identity rejects before launching capture/hardware. No speakers, real USB device, sweeps/noise, unmute or live playback used.
- Executed `scripts/rollback --remove`: only project MA container/network removed, state retained; redeployed the same pinned image and confirmed a project-only persistence marker survived. All four scanner containers stayed up. Full version/state-migration rollback remains configured, not tested.

## Configured / unverified

Native dedicated PW48k sink, monitor→Stdin bridge, WP no hardware monitors, Squeezelite direct MA and explicit sink, direct UMC ALSA20-channel assumed device schema, systemd failure dependency/restart definitions, serialized/audited DSP and authenticated MA CLI. Actual PipeWire/WP/ALSA-plugin/Squeezelite runtime compatibility is pending package installation; no claim of end-to-end local playback or silent hardware routing.

Rollback remove script retains all state; release rollback requires migration-reviewed state backup. Full native service/reboot/USB recovery and long-running CPU/underrun/clipping observations are not tested. No actual acoustic correction/video latency/mic-calibration measurement.

## User / administrator blocked

1. User executes reviewed `sudo bash /home/zach/garage-audio/scripts/admin-prerequisites`; existing `sudo -n` requires interactive authentication. Native dependencies absent; Linger=no and audio group missing. No privileged Docker workaround.
2. UMC1820/UMIK absent: physical attach, stable ALSA identity, exact channel/format/jack-map verification and low analog gain commissioning remain attended. Only internal/HDMI exists and is never selected.
3. User MA onboarding and securely stored API token, source/transport provider setup; Spotify account/engine/terms/auth and bridge-vs-host-network decision. No secrets supplied/collected.
4. Explicit attended authorization for actual quiet channel/playback test, unplug/replug, room measurement and any reboot. Reboot not requested or performed.

Scanner's four existing host-network containers were inspected, stayed running, and were never modified. No root/network/SSH/firewall/reboot changes.
