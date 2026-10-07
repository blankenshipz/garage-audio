# Calibration and local video — not commissioned

The shipped profile has **no measured PEQ, room correction, or acoustic delays**.
The 80 Hz LR4 split is a safe starting template, not a conclusion about these speakers.

## Attended REW workflow

1. Verify real UMC output/jack mapping and gain staging first. Keep DSP master muted/-60 until fresh approval to emit sound. No automated sweeps or noise in deployment/tests.
2. Identify the exact UMIK model/serial. Download its manufacturer serial-specific calibration file yourself; use the 0-degree file with mic aimed at the source for on-axis work, or the 90-degree file with mic vertical for listening-position room measurements. Do not mix orientation files. No calibration file is invented or included.
3. REW input is the calibrated USB mic; output must enter the same `garage_music` virtual sink/DSP chain, not the raw DAC or internal/HDMI audio. Use a full-range main speaker for REW's **acoustic timing reference**, at a verified audible reference level; a subwoofer cannot reproduce the high-frequency timing-reference chirp. Measure left, right and sub with a consistent timing reference/mic position. USB mic and DAC have independent clocks; follow REW's timing-reference/clock-correction guidance. Do not mistake relative acoustic delays for electrical channel latency.
4. Measure one branch at a time with auto-stopping low-level sweeps after explicit authorization. Check clipping and ambient noise. Then measure crossover summation and multiple listening positions, not only a single narrow null. Set distance/delay/polarity from evidence, not tape-distance guesses; respect the profile's 0..50 ms delay range.
5. In REW EQ choose **Generic** and export text filters. This importer supports enabled **PK** only, max ten/channel, 20..20000 Hz, Q 0.1..20, gain -20..0 dB. Boosts, shelves, invalid/unsupported enabled filters are rejected. Disabled filters are skipped. The output is a candidate profile, not automatically applied or unmuted.

```sh
venv/bin/python -m garage_audio.rew profiles/uncalibrated.json sub my-rew.txt profiles/measured-candidate.json
venv/bin/python -m garage_audio.control profile profiles/measured-candidate.json
    # If previously muted, stays muted at prior bounded volume; failures reset-60.
```

Importer and bounds were exercised with accepted cut-only PK, rejected boost, and rejected shelf text. Real REW exports/measurement files and actual mic behavior remain unverified. Keep raw `.mdat`, mic calibration provenance, positions, levels, old profile and new measurements privately. Never call a generated template a measured correction.

## Future local video path

Direct decoded PCM → `garage_music` → monitor capture → same Camilla crossover → UMC. Bypass MA/Squeezelite buffering for video; do not bypass the DSP. A native video player can use the explicit ALSA PCM `garage_music` with `ALSA_CONFIG_PATH` and the dedicated PipeWire runtime/remote environment from `garage-player.service`. Exact player flags and access to video sources need that player's actual version verification; no video source/player is currently installed or tested.

Start with 1024-frame quantum/chunks (~21.3 ms each at 48k), then measure full display/audio latency using a real local A/V synchronization source. Add an intentional video/audio offset only from measured end-to-end results. Smaller chunks can improve latency but increase underrun/CPU risk; prove under load. MA queue buffering is not an A/V clock. **No measured lip-sync, video latency, room response, or mic calibration acceptance exists yet.**

References: https://www.roomeqwizard.com/help/help_en-GB/html/impulseresponse.html ; https://www.minidsp.com/products/acoustic-measurement/umik-1
