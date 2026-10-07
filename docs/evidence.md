# Phased evidence (self-reported, 2026-10-06/07)

## Deployed / executable tested

- New git project `/Users/zach/Code/garage-audio`, staged `/home/zach/garage-audio` on verified garage-server.
- MA 2.10.5 image digest `sha256:28023f8c0d96ca2496391f3218a6d70d7ef3ba84db846b3a3f3cd9ce7ef8aedc`, user1000, caps dropped, nonprivileged, loopback8095/8097/3483, persistent state, unless-stopped restart policy. HTTP302 on live UI endpoint (not authenticated UI journey proof). Official client reached server schema65 and rejected missing authentication token as expected.
- CamillaDSP4.1.3 official release archive hash `55f5ec2ed80fcc79a543672f9f89ace4557d290d80584ef31ee0442111bd0b11`; executable prints4.1.3/05e9cfc, websocket+RawFile/WavFile/Stdin/SignalGenerator/ALSA capture, File/Stdout/ALSA playback. Native PipeWire backend **not used**; native PW monitor bridge feeds Stdin.
- Host lacks libasound; extracted official Ubuntu `libasound2t64 1.2.15.3-1ubuntu1.1` only under project tools, hash `96e20cf5c9095ef54ba46fccffea5c01cf2de17a1012c5683a67e43504a6d726`. No host package installation or sudo bypass.
- Eight real-binary executable DSP tests now add crossover40..160Hz and PEQ add/replace/delete/clear validation/PCM/readback, successful muted-volume preservation, successful existing unmute+volume preservation only with attended flag, invalid/unauthorized transaction reset-muted/-60/known-good rollback, actual serialized crossover/PEQ CLI against explicitly restricted Stdin→FILE/null, detached commissioning guardian/timer and restart-muted/-60. The unchanged LR4/master/headroom/sum/channel/sub/17-zero/PEQ-bypass/native-ceiling/REW tests remain applicable; final suite binds the corrected source artifact. File-test CLI refuses production port or any hardware backend.
- Actual isolated user-local PipeWire1.6.2/WirePlumber0.5.13/Squeezelite2.0.0-1541 with official Ubuntu libraries extracted only into `tools/native/root` ran successfully: sole48000 virtual sink/no hardware nodes, explicit Squeezelite ALSA→PW PCM, synthetic PCM→sink-monitor→real Camilla Stdin→20-channel FILE (L/R/sub nonzero,17 unused exactlyzero), missing explicit monitor target fails, sink recreates after actual PW restart. No host package installation, native services, DAC or MA provider/player/queue acceptance. `tools/native/packages.sha256` records observed extracted package archive hashes. Bounded pw-record sample-count returns1 with no error and full192000 frames in this observed version; test distinguishes it from target errors by actual PCM length and logs.
- Runtime investigation corrected missing PW access/metadata/SPA-node/driver modules and required `node.dont-move=true` to prevent WP metadata retargeting, explicit raw PCM, camera/MIDI monitor disabling, and explicit48-only Squeezelite range48000-48000. Its pre-server ALSA open initially44100 is converted by PW; actual MA negotiation pending authentication/provider setup.
- ALSA hardware config **schema-only** checked via `camilladsp -c` without device opens. `scripts/run-dsp` without commissioned identity rejects before launching capture/hardware. No speakers/real USB audio-device opens, audible playback, hardware unmute, sweeps/noise, calibration or live hardware playback. Synthetic FILE/null unmute tests are not physical acceptance.
- Executed `scripts/rollback --remove`: only project MA container/network removed, state retained; redeployed the same pinned image and confirmed a project-only persistence marker survived. All four scanner containers stayed up. Full version/state-migration rollback remains configured, not tested.

## Configured / unverified

Dedicated DAC UID/root-owned launcher/system service, exact serial-specific udev ownership/mode0600/no-uaccess, write-only user PCM FIFO, hardware identity/sysfs/permission admission and root rollback are staged-only. Private graph and exclusive open alone are expressly not a DAC-permission boundary. Systemd unit parser observed missing staged executable (expected; no root installation), not a live service success. Actual device ownership/ACL/open and cross-manager failure/restart enforcement require attached UMC and administrator provisioning.

Direct MA Squeezelite provider/player, authenticated status/queue/URL commands and sources remain unconfigured without user account/token; bridge topology is unchanged. No claim of end-to-end MA playback or silent hardware routing. Hardware20-channel/S32/card/PCM/jack mapping remains unverified and must fail rather than silently downmix/fallback.

Rollback remove script retains all state; release rollback requires migration-reviewed state backup. Full native service/reboot/USB recovery and long-running CPU/underrun/clipping observations are not tested. No actual acoustic correction/video latency/mic-calibration measurement.

## User / administrator blocked

1. User executes reviewed `sudo bash /home/zach/garage-audio/scripts/admin-prerequisites`; existing `sudo -n` requires interactive authentication. Host native dependencies absent and Linger=no. Revised prerequisite never grants zach audio/DAC membership. Separate staged `admin-dac-isolation` requires actual verified USB VID/PID/serial, and adds only write-only stream group access. No privileged Docker workaround or root provisioning performed.
2. UMC1820/UMIK absent: physical attach, stable ALSA identity, exact channel/format/jack-map verification and low analog gain commissioning remain attended. Only internal/HDMI exists and is never selected.
3. User MA onboarding and securely stored API token, source/transport provider setup; Spotify account/engine/terms/auth and bridge-vs-host-network decision. No secrets supplied/collected.
4. Explicit attended authorization for actual quiet channel/playback test, unplug/replug, room measurement and any reboot. Reboot not requested or performed.

Scanner's four existing host-network containers were inspected, stayed running, and were never modified. No root/network/SSH/firewall/reboot changes.

## Scope correction requiring parent review

Initial isolated WP invocations used its default `/home/zach/.local/state/wireplumber`, whose observed mtime was2026-10-07T01:31:41Z. This incidental policy-state write was **outside the worker's intended `/home/zach/garage-audio` write footprint**. XDG_STATE_HOME/XDG_CACHE_HOME are now explicitly project-scoped in tests and staged service. No contents of the default directory were inspected or removed; prior existence/contents were not established, so no safe original-state restoration is claimed. Parent must authorize a narrowly scoped cleanup/restoration review rather than assuming the artifact writes were perfectly scoped. All other intended implementation/deployment writes remain project-only; no root writes.
