# UMC1820 silent preparation — 2026-10-09

This is a staged hardware candidate, **not an active audio setup**. No PCM
stream, playback, capture, tone, mixer write, native service start or restart
was performed. Existing dashboard, Music Assistant and scanner containers stayed
unchanged by this work. At the final readback the scanner OP25 container retained
its ID but reported a shorter uptime; no scanner action was performed or
investigated in this audio scope. The historical README's hardware-absent
description is now outdated.

## Live metadata

- USB `1397:0514`, serial `D72E24B7`, `snd-usb-audio`, USB path `1-3`.
- ALSA card ID `UMC1820`, PCM 0: `hw:CARD=UMC1820,DEV=0`. Card index 1 is
  incidental and must not be used as a stable binding.
- Advertised playback: **12 channels, ALSA S24_3LE**, 24 bits.
- Advertised capture: **10 channels, S32_LE**, 24 significant bits.
- Both advertise 44100, 48000, 88200 and 96000 Hz. Both stream states were
  Stop/closed before and after preparation. Physical jack mapping is unverified.
- Playback node root:audio mode0660; zach has no write access. `garage-dsp`
  account and installed `garage-audio-dsp` unit are absent. No master mute or
  physical gain setting has been verified.

There are ten physical analog outputs (MAIN1/2 and LINE OUT3–10); digital
channels are not extra analog jacks. The current 12/10 metadata is consistent
with the manufacturer's SPDIF optical mode. Confirm the front-panel OPT I/O
position physically; do not silently switch it. ADAT at48k advertises additional
channels, and changing that switch reboots the interface. Reinspect metadata
after any switch change. No optical expansion is needed for three analog feeds.

## Staged, reversible configuration

`scripts/prepare-umc1820-silent.py` reads USB/sysfs/proc metadata only, refuses
open streams or unreviewed endpoint layouts, and creates a new exclusive
directory under ignored `state/hardware-preparation/`. It never replaces an
active identity/profile, installs a service, changes device ownership or starts
Camilla. Remove only its newly created directory to undo the staged candidate.

Observed server output:
`/home/zach/garage-audio/state/hardware-preparation/20261009T202949.196218Z/`.
The earlier `20261009T202203.601447Z` preparation remains retained unchanged.
The Camilla candidate is48k, 12-channel S24_3_LE, stable CARD/DEV binding,
OUT3/4/5 intent, all three branch filters muted, other nine channels unmapped,
-6dB headroom and existing LR4 80Hz crossover. It does not persist an unmute.
Required eventual startup remains master muted/-60; that runtime state is NOT
established by a config file.

Official Camilla4.1.3 `--check` accepted the JSON candidate, exit0, using only
the already extracted project-private ALSA library. This flag validates the
schema and exits without device startup. Log and exit receipt remain beside
the candidate. Static readback verified format/rate/binding, routing destinations
2/3/4, branch mutes, headroom and crossover. Candidate SHA256:
`f8e7a59b080ee0274aab33dd0ae0c17d6d8d303f763c2932465ae4cdc8e2b2af`.

The source daemon/compiler now supports a pinned capability policy. It reads
root-owned, nonsymlink, nonwritable `/etc/garage-audio/playback-capabilities.json`
and compares exact identity/channel count/format/48k against current USB ancestry
and descriptor metadata before launching DSP and before hardware-authorized graph
changes/unmute. No client IPC field can choose capabilities. Missing policy
retains legacy20/S32 expectations, which correctly refuse the current12/S24
device. Unknown or ambiguous endpoint formats/layouts, wrong serials, missing
cards and changed optical-mode metadata fail closed. Dedicated garage-dsp owner/
mode0600 checks remain unchanged. A physical-map receipt cannot be reused across
a different capability snapshot (or a missing old snapshot); administrator
revalidation is required, and this work writes no physical-map receipt.

The reviewed root-private provisioning source can now pin these observed values
with `--playback-channels 12 --playback-format S24_3LE`, alongside its existing
verified USB identity parameters. Defaults retain legacy20/S32. **It has not been
executed.** One next administrator step is to review the refreshed source/bundle
and independently supplied hashes through the existing DAC-isolation bootstrap
workflow, then provision the observed identity and capability policy without
enabling/starting audio. The historical privileged bundle was not rebuilt or
relabelled as new proof. No mutable project script should be executed with sudo.
Do not grant ordinary applications audio-group access or use a default/HDMI fallback.
The host's direct Camilla invocation currently lacks system `libasound.so.2`;
project-private libraries suffice for schema checking, not proof of installation.

## Source verification and retained receipts

Updated `garage_audio/hardware.py`, `profile.py`, `daemon.py`,
`scripts/provision.py`, and `scripts/prepare-umc1820-silent.py`; added
`tests/hardware_capabilities.py`. Remote originals were hash-compared before
replacement and retained under
`state/hardware-preparation/source-backup-20261009T2030/`.
No installed privileged files, active profile or existing candidate were altered.

The six-file source inventory digest (sorted filename + NUL + bytes + NUL) is
`13c89231c859b8b0c3709a4c1aebc1a8f48bf0aa1b8f34a601b888a0f79bd5b0`.
All **11 isolated descriptor-fixture tests** passed, including packed24/12,
legacy20/S32 equivalence and live-metadata mismatch refusal, optical switch
change, wrong serial, disconnected/ambiguous card, unknown format/rate/layout,
multiple alternate settings, policy immutability, compiler-binding mismatch,
and stale physical-map snapshot rejection. These are synthetic metadata fixtures,
not hardware audio tests. Private local receipt:
`umc-capability-tests-v2-ng0s4v4c/receipt.json` in the harness check-receipts directory.

Fresh server preparation uses the updated production compiler/probe. Official
Camilla4.1.3 `--check` again passed exit0. Fresh descriptor admission and exact
compiled-config/readback passed; legacy default admission correctly refused.
`readback-receipt.json`, schema log/exit and source inventory are retained beside
the candidate. Capability-policy SHA256:
`adfbecab9427748a38fa137adba8176691150375e3a6edb41365d10ecd2a4370`.
Candidate bytes have the same SHA256 as the earlier graph, but the source and
capability policy have separate fresh identity/proof. Playback/capture remained
closed; DSP service remained not-found/inactive; device write access remained
false for zach. Runtime enforcement and acoustic commissioning remain unrun.

## Wiring handoff (recorded models: stereo Fosi V3, R-28F, R-12SW)

Keep Fosi power disconnected/off, its volume minimum, and sub unplugged/off
while connecting. If the physical model differs from these prior records,
stop and provide its label/photo before using this diagram.

```
UMC rear LINE OUT3 -> suitable mono 1/4-inch line-to-RCA -> V3 RCA LEFT/white
UMC rear LINE OUT4 -> suitable mono 1/4-inch line-to-RCA -> V3 RCA RIGHT/red
V3 LEFT speaker +/- -> speaker wire -> left R-28F matching +/-
V3 RIGHT speaker +/- -> speaker wire -> right R-28F matching +/-
UMC rear LINE OUT5 -> suitable mono line-to-RCA -> R-12SW LEFT/LFE RCA
```

The V3 is RCA-input stereo, not the balanced V3 Mono. UMC LINE OUT3–10 are
balanced TRS line outputs; use an interconnect explicitly compatible with a
balanced source feeding an unbalanced RCA input. The existing plan calls for a
long TS-to-RCA sub cable, but the reviewed UMC guide does not document its
unbalanced output wiring: confirm the cable/adapter compatibility rather than
improvising a ring-to-ground short. One jack is one mono channel, not stereo;
do not use a headphone-style stereo breakout or combine L/R with a Y cable.

Keep V3 pre-out unused in this DSP plan: OUT5 supplies the separately filtered
sub. V3's knob does not control its pre-out. Never connect amplified speaker
terminals to interface inputs or sub RCA; never join speaker channel negatives.
Keep passive-speaker terminal jumpers as specified for ordinary single wiring.

UMC MAIN OUT knob, DIM and MUTE control MAIN1/2 only: **they are not a safety
mute for LINE OUT3/4/5**. Those levels are controlled by software. Turn the
MONITORING mix to PB1-2 to exclude direct inputs from its monitor mix; it does
not attenuate LINE OUT3–10. Use the supplied UMC power adapter. Do not enable
phantom48V for a USB measurement microphone.

R-12SW LOWPASS should be fully clockwise160Hz when DSP provides the crossover
(per its manual), not an invented LFE bypass. Keep sub gain minimum until the
attended setup; document its phase position and determine integration by
measurement, not a guessed optimum. Leave all amplifiers off until the software
is installed and an attended low-gain channel check is explicitly approved.

Remaining: confirm physical models/cables/OPT I/O position; review/install
the refreshed reviewed isolated native DSP and capability policy; confirm measurement mic, stand and its
serial-specific calibration file; then authorize attended finite low-gain
mapping, gain matching and room measurements. No sound quality or calibrated
channel mapping claim is made here.

## Manufacturer sources

- [Behringer UMC1820 Quick Start Guide](https://cdn-media.empowertribe.com/2b9a46d7e1104b78b8881f726c18a4d0/QSG_BE_0805-AAN_UMC1820_WW.pdf), controls7–13/20–26.
- [Fosi stereo V3 instructions](https://fosiaudio.com/pages/user-instruction-v3), RCA connection/polarity/power sequence and pre-out FAQ.
- [Klipsch R-12SW manufacturer manual](https://d2um2qdswy1tb0.cloudfront.net/product-manuals/Reference_Subs_10_12-Manual-v03.pdf), Connections and Controls.
