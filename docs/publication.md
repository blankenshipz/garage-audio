# Publication and capability boundaries

The Python sdist is the source/deployment input bundle; the wheel contains the
`garage_audio` package plus web/profile assets, not native DSP or system services.
`MANIFEST.in` defines curated source inputs. `.env.example` contains documentation
addresses and credential references only. `.env`, private state, caches and local
`opencode.json` are never shipped. Do not include the latter in local publication
commits; it is harness configuration, not application configuration.

## Source capability map

| Journey | Backend | MCP | UI | Acceptance boundary |
|---|---|---|---|---|
| Discover/setup | `status`, `capabilities`, `setup.*` | status/capabilities, generic operation | Connect/Check, setup | Discover actual deployment; source is newer than baseline |
| Silence/readiness | `audio.status/pause/stop` | generic operation | speaker/status controls | Public socket, no raw DSP; never implies unmute authority |
| 2.1 configuration | `audio.config.*` | generic operation | speaker configuration | Persistent graph/rollback software tested; sub jack uncommissioned |
| Rooms and REW import | `room.*`, listening profiles, `measurements.import` | room/profile/calibration tools | room/measurement forms | Labels/photos are not distances or measured sound |
| Bounded acquisition | `measurement.hardware.status`, `jobs.*` | generic status, measurement job tools | job readiness/progress/cancel | Real ALSA adapter; fixture tests, no qualified live microphone |
| Capture import | `calibration.capture.import` | `calibration_capture_import` | job/import workflow | Qualified measured capture only; not synthetic promotion |
| Correction lifecycle | `calibration.correction.prepare/get/apply/rollback/verify` | five `calibration_correction_*` tools | improve/verify controls | Local paired/software proof; hardware apply authority absent |
| Inference | `agent.*` disabled-state operations | generic operation | honest disabled state | No model/vision calls, no paid fallback |

This map describes source entry points, not end-to-end deployed proof. Query exact
schemas and readiness; methods may refuse actions safely. Baseline remote SSH MCP
read/discovery/refusal/reconnect was exercised with 34 tools/five resources/81
operations. New six-tool/seven-operation lifecycle additions and rendered local UI
proof belong to a newer candidate; activation and matching live acceptance remain.

## Release gates

Pause package-input owners, freeze inventory/digest, retain clean-room build logs,
then run gates against that exact candidate. Any source change invalidates affected
build/package gates. Keep transient receipts outside shipped directories. Preserve
vendor sources/notices and private working auth/state; no automatic cleanup.
Packaging determinism does not imply independently reproducible upstream binaries,
an offline dependency installation, licensing clearance, or acoustic verification.
