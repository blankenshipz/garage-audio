# DAC access boundary — staged, not enforced yet

A private virtual sink does not revoke `/dev/snd` access. An ALSA exclusive open excludes another ordinary opener only while Camilla owns that stream. Giving zach `audio` would let normal same-user applications bypass DSP whenever it closes. That design is **not** used in the revised administrator prerequisite.

Staged implementation:
- Separate non-login `garage-dsp` user, no `audio` group; root-owned pinned Camilla/launcher under `/usr/local/libexec/garage-audio`.
- Root system unit runs as that user, filesystem hardening and ALSA-device cgroup allowance. It starts `--wait --no_config --gain=-60 --mute`, not a mutable persisted master state.
- Serial-specific USB-ancestor udev rule makes only matched UMC playback/control nodes owner `garage-dsp`, mode0600, removes `uaccess` tag. No broad vendor-only match and no scanner/internal/HDMI rule. Kernel permission prevents normal zach/MA/Squeezelite/video apps from directly opening those nodes even while DSP is down **once actual node owner/mode/ACL are verified**.
- `garage-stream` gives zach write-only access to `/run/garage-audio/capture.fifo`, not DAC permissions. User bridge writes explicit PW monitor PCM; dedicated daemon reads stdin and opens hardware. The user-facing graph/CLI remains under zach's project.

No accounts/groups/udev/root services were created by this worker. UMC is absent; its actual vendor/product/serial, ALSA card/PCM count and ACL behavior remain unknown. If it lacks a unique serial, stop and resolve an appropriately stable identity with the parent; do not substitute a wildcard that could seize an unrelated device.

After attended exact USB identity discovery, administrator reviews and runs:

```sh
sudo bash /home/zach/garage-audio/scripts/admin-dac-isolation VERIFIED_VID VERIFIED_PID VERIFIED_SERIAL
```

Script verifies the official archive hash, installs root-owned definitions, creates dedicated identity/stream group, reloads rules but **does not trigger existing nodes or start/enable audio**. If zach already belongs to audio it stops for review instead of silently removing unrelated authorization. Fresh login refreshes group membership. Existing-node ACL/ownership requires an attended rule apply/replug after explicit authorization; replug is not authorized now.

Before any start, record `stat`/ACL/sysfs for **only** the actual UMC nodes and confirm zach/application identity lacks direct read/write access while DSP is stopped. Do not test by opening DAC or emitting sound unattended. Then root daemon and user graph can be enabled/started in a separate authorized phase. Actual permission/open checks, boot behavior and disconnect/reconnect recovery are untested.

Record actual metadata in `state/hardware-identity.json` with exactly `model:"UMC1820"`, lowercase four-hex-digit `vendor`/`product`, real unique `serial`, actual stable `alsa_card` and integer `pcm`. `state/alsa-device` must exactly equal `hw:CARD=<alsa_card>,DEV=<pcm>`. Do not guess VID/PID/serial or save a synthetic identity. A Linux card ID may be `U1820` or another actual spelling; it need not contain the marketing name. Before startup, profile mutation, commissioning or normal unmute, admission compares sysfs USB ancestry/serial to this record and stats the exact playback node for owner `garage-dsp` and mode0600. It never opens a device. Identity validation has isolated synthetic-schema tests, but actual matching/permissions remain unverified while UMC is absent.

Trust limitations: root/docker membership can defeat host DAC permissions; Docker MA currently has no devices or privileges. Local Camilla websocket remains unauthenticated on127.0.0.1: trusted local administration can alter DSP configs. Dedicated device identity is not a sandbox against a malicious authorized controller or root/docker user. No claim of an adversarial security proof.

Rollback: user `scripts/rollback --remove` closes project PCM producer/player and stops MA, but cannot stop a root-owned daemon without authorization. Administrator separately runs `sudo bash scripts/admin-dac-rollback`, stopping/disabling only this root service, restoring any original unit/rule backups, reloading definitions. It does not trigger devices/reboot/remove unrelated packages. Accounts/groups/binary retained; attended device ownership/ACL restoration may still be required. Root provisioning/rollback are staged-only and not executed or claimed tested.
