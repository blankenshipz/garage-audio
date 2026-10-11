# Immutable DAC policy boundary — staged, not installed

The former unrestricted host-loopback websocket and mutable `sudo bash project-script` installer were unsafe and superseded. Do **not** install earlier releases. No root provisioning, root namespace, hardware opens or DAC ownership changes have been performed.

## Policy enforcement

Root-owned `garage-audio-dsp.service` runs a separate non-login `garage-dsp` identity with `PrivateNetwork=yes`. Raw Camilla websocket still binds127.0.0.1:1234 as required, but **inside that private network namespace**, inaccessible to ordinary host/LAN applications. Its only caller is the immutable policy broker inside the same namespace. No raw websocket is published/proxied into the host namespace, and no general REST API exists.

Host control uses filesystem Unix socket `/run/garage-audio/control.sock`, mode0660/group`garage-control`, with Linux SO_PEERCRED enforcing the provisioned administrator UID or root. Legacy attendance-string unmute/commissioning is denied. Only the immutable root human helper, after independent password authentication and operation review, can arm a120-second -60dB stereo lease on outputs3/4; sub and all other outputs remain silent. A separate guardian checks helper pidfd/deadline without DSP calls or broker locks and kills only the owned DSP on owner loss. Graph/device readback runs separately. No arbitrary config/ceiling/device/pipeline/path command is forwarded. Root/docker administrators remain outside the adversarial boundary.

Serial-specific USB udev playback/control nodes are owner`garage-dsp`, mode0600/no-uaccess. zach/MA/Squeezelite/video have no audio/DAC group; only `garage-stream` write access to PCM FIFO and permissioned `garage-control` IPC. Root-owned launcher/code and offline SDK venv are not writable by either DSP UID or normal apps. All service startup uses isolated Python `-I` with a literal root-owned import path, not user PYTHONPATH/scripts/config. Mutable profile state is data-only, schema validated, under `/var/lib/garage-audio-dsp` ownerDSP/mode0700. Root-owned identity is `/etc/garage-audio/identity.json`, not a user-writable binding.

Every lifecycle initialize and profile uses the **same broker control.lock**. Initialize increments generation and always mutes/-60. Requests carry observed generation; stale requests are rejected. Transactions capture owned-DSP PID/lifecycle identity, commit/read back durable active+known-good profile atomically while muted, check generation again, and restore permitted mute **last**. Failure proves mute/-60 **before** graph rollback; inability to prove mute kills only owned DSP. Producer EOF terminates that DSP generation. Broker/service recovery starts fresh namespace/generation/mute, never old unmute.

## Verified installer, not mutable sudo code

The revision8 hashes and command below are a **historical frozen candidate**, not
authorization to reinstall the current live services. Later October11 playback
work records installed native audio and a different scoped Connect deployment.
Fresh installation requires a new source/bundle inventory and independent trusted
pins; do not run the old example merely because it remains in this document.

`scripts/provision.py` is executed **only after** independent hash verification in a root-private snapshot. The revision8 candidate is `state/native-playback-receipts/20261010-native-review-r8/admin-release.tar.gz`; earlier a9b091/ea78f696 candidates are superseded. It contains allowlisted root code/unit/helper/rollback, official Camilla archive, pinned ALSA library and three pinned SDK wheels. Bundle SHA256 must come from the independently reviewed handoff, **not merely an adjacent mutable README/manifest**. Snapshot uses O_NOFOLLOW into a root-private directory and verifies copied bytes before strict extraction. Existing privileged destinations are checked for ownership/mutability and escaping symlinks. Documentation/progress receipts are external, not package inputs.

**Staged only: parent safety review must approve these new exact bootstrap/bundle hashes before administrator execution.** Bundle SHA256: `d1f15dbb3ed54ae3c4763eec0d41f594c2535898f2579070b409ebc22de38e2b`. Bootstrap SHA256: `94088e63b214685e6fc0c49ab039366e4bc612bc08eaedb519266aebaab756c6`. Independently verify these against the trusted review handoff; the mutable document alone is not authority. Exact observed identity/capability arguments follow (48kHz is enforced in bootstrap/compiler; no20/S32 default):

```sh
sudo /bin/bash -c 'set -eu; umask 077; d=$(mktemp -d /root/garage-audio-bootstrap.XXXXXX); cp -P "$1" "$d/provision.py"; test -f "$d/provision.py"; test ! -L "$d/provision.py"; printf "%s  %s\n" "$2" "$d/provision.py" | sha256sum -c -; exec /usr/bin/python3 "$d/provision.py" --bundle "$3" --sha256 "$4" --vendor 1397 --product 0514 --serial D72E24B7 --card UMC1820 --pcm 0 --playback-channels 12 --playback-format S24_3LE' _ /home/zach/garage-audio/scripts/provision.py 94088e63b214685e6fc0c49ab039366e4bc612bc08eaedb519266aebaab756c6 /home/zach/garage-audio/state/native-playback-receipts/20261010-native-review-r8/admin-release.tar.gz d1f15dbb3ed54ae3c4763eec0d41f594c2535898f2579070b409ebc22de38e2b
```

No unknown/synthetic identity, wildcard or marketing-name guess. Installer accepts lowercase verified VID/PID, unique serial and actual stable ALSA card/PCM; if UMC lacks a unique serial stop for a root-approved stable alternative. It creates dedicated/control/stream groups, installs verified private assets/offline venv/root identity, reloads definitions only; **does not enable/start/trigger/replug/reboot**. Host package prerequisite must use directly reviewed literal administrator apt/loginctl commands, not execute mutable project shell code. Existing normal-user audio membership causes review/refusal, not silent authorization removal.

Attended follow-up must verify exact sysfs identity, node owner/mode/ACL and root policy namespace before starting. Root daemon and user graph installation/start require expanded authorization. Raw namespace and UID/device/open/cgroup behavior of the actual systemd service remain unverified; never infer these from source/unit parsing or Docker alone.

## Real versus injected proof

`tests/safety.py`: real Camilla FILE/null and official client with injected persistence failure, generation change after durable commit, lifecycle reset/stale request, unreachable client/IPC producer stop; rollback reloaded only muted/-60. Actual Unix SO_PEERCRED denied wrong UID. Snapshot/invalid archive tests are isolated filesystem tests, never privileged installation.

`scripts/test-policy-namespace`: real nonprivileged project Docker container (`--network none`, UID1000, capsALL dropped, no devices/ports), raw private loopback unreachable from host, permitted Unix status works and arbitrary config/ceiling bypass rejected with real FILE/null DSP. This proves the real namespace/IPC pattern on Linux, **not installed systemd or DAC permissions**. No privileged Docker sudo workaround.

Rollback after approved future install: user removal closes PCM/player/MA; administrator executes only root-owned verified `/usr/local/libexec/garage-audio/admin-rollback`, not user project script. It stops/disables only root DAC service and restores original unit/rule backups. No device trigger/reboot/package removal; attended ACL/ownership restoration may still be required. Root rollback is staged-only, not executed.

## FIFO and producer follow-up repairs

FIFO creation now explicitly chmods/verifies final0620 and exact owner/garage-stream group; mkfifo mode alone was masked by default0022. A real Docker writer UID1001/GID1000 successfully wrote to ownerUID1000/GID1000 FIFO created under0022. This proves distinct-UID group-write semantics, **not provisioned garage-dsp/garage-stream identities**.

User bridge invokes `garage_audio.producer`, registering only its own exact PCM child's UID/boot/starttime/exe/argv/cwd/random instance marker under a shared start/stop lock. Independent fail-close validates/signals the exact lifetime via Linux pidfd and **confirms exit before any latch write/fsync or manager call**. Manager or disk errors cannot skip verified termination. Latch persistence failure is separately reported, not claimed durable. Existing producer registration is retained as a tombstone: ANY existing registration blocks new startup, even if exited and no latch was written. Explicit `--rearm` first proves policy muted/-60, refuses a live/unverifiable registered identity, then removes the exited registration/latch. Supervisor exception handling likewise kills/reaps its owned child before lock acquisition/persistence. Watchdog closes PCM on IPC loss/generation change; PDEATHSIG closes PCM on supervisor crash. No arbitrary PID/name/group kill. Forged/stale identity refuses signals. Every later start initializes muted. Actual service/ACL/hardware effects remain unverified.

`tests/producer.py` exercised real continuous FILE/null writers and verified process exit plus stable file size: actual nonzero/timeout/absent manager commands, real missing policy and user-bus sockets via CLI, watchdog control-loss injection and uncatchable supervisor SIGKILL. UID/starttime/marker mismatch rejection kept the real child alive until legitimate stop. All8 passed0.983s. Path/env and watchdog-event injections are isolated seams, not mocked termination; actual root systemd/DAC behavior remains untested.
