# Explicit attended commissioning — pending real hardware

No commissioning was performed. `physical-map-verified` must describe actual evidence, never be fabricated to unlock controls.

1. Parent/user confirms presence, correct wiring and low/fixed Fosi V3 and R-12SW analog gains. Attach/identify UMC/UMIK only with authorization. Finish reviewed dedicated-DAC identity provisioning and verify exact permissions/channel capabilities. Put actual ALSA identity in `state/alsa-device`. Start dedicated daemon and native user graph muted/-60; status must read back the expected UMC graph and LR4 filters.
2. Stop all MA queues. Disable normalization/crossfade/automatic gain and use fixed unity transport. Prepare a **finite, quiet, approved** local PCM test clip of no more than3 seconds, containing suitable main/sub content and no full-scale impulse/noise. Do not use MA buffering for commissioning or an unbounded radio stream.
3. While muted, direct that finite clip **only** to `garage_music` with the native graph's runtime/remote/ALSA environment. It is acceptable for the clip to begin with silence so the attended window can be aligned. No CLI below generates or plays a signal by itself.
4. With fresh attended approval, use the temporary window for one branch:

```sh
venv/bin/python -m garage_audio.control commission-window left --seconds 3 --attended PRESENT-GAINS-LOW
# Repeat right/sub only after individually approved observation; do not automate.
```

Requires a currently muted, explicitly configured UMC ALSA graph, but intentionally does **not** require a previously verified-map marker. Compiler temporarily mutes other branches and fixes master-60. The privileged policy broker starts a hard1..5second timer **before** requesting unmute; deadline kills only its owned DSP process, ending the generation, rather than relying on a possibly unreachable websocket mute. Original profile is not overwritten. Restart/reinitialization loads the original durable graph **muted/-60**. The CLI reports DSP_STOPPED; service recovery/user bridge may need restarting before the next window. No source is generated automatically. Actual systemd/hardware fault effectiveness remains unverified.

Never raise the software commissioning master above-60 in this pre-map workflow. If too quiet to establish mapping, stop and obtain a revised attended plan; do not defeat guardrails or invent a result. Stop the finite source and inspect `status` after each window. Keep emergency power/mute available at the amplifiers, independent of software. Verify OUT3 left FosiV3/R-28F, OUT4 right, OUT5 R-12SW LFE TS-RCA, and all17 unused channels. Record actual results before marking verification.

The broker deadline/owned-process exit was tested only with **FILE/null** synthetic PCM; the older detached guardian remains isolated-test code, not the production safety boundary. No DAC/analog-gain/jack verification. Root/user service failures/USB faults need separate attended proof. Normal `unmute --attended PRESENT-GAINS-LOW` stays locked until administrator records a real `/var/lib/garage-audio-dsp/physical-map-verified` receipt. Successful already-unmuted profile changes additionally need `--attended-profile PRESENT-GAINS-LOW` and matching DSP generation; errors/restarts never restore unmute.
