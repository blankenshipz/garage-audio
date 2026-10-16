# Silent real-source qualification (not physical acceptance)

Apply `compose.audio-scalar.yaml` LAST to the reviewed frozen operator boot plan.
It clears sound devices, device cgroup rules, added capabilities and hardware-policy
mounts. Inspect the actual created container before startup, not merely YAML. Use
the unchanged MA/app images and a reviewed rebuilt audio image; no live source overlays.
Disable network factory automatic restart for this bounded qualification transaction.
No hardware restoration occurs at expiry. Normal hardware boot is a separate explicit
operator transition after gain qualification; never restore it automatically.

`--mode scalar` requires current root-owned network generation authority. All eight
private FIFO readers must be ready before exposing event/public listeners. Each DSP
inherits exactly its owned, verified FIFO descriptor and opens `/proc/self/fd/N`;
it does not open the replaceable FIFO pathname. There is no regular-file PCM fallback.
Each fresh Play/resume consumes one segment; exhaustion, FIFO mutation, reader loss,
child loss or network loss closes owned children. The hard 300-second budget begins
at the first admitted fresh Play and never resets on Pause/Resume. The wrapper uses
the same parent-death fencing for capture, input meter and DSP. Scalar mode refuses
hardware policy/node mounts and never constructs a device lease.

Qualification now requires a separate root-owned0444 read-only policy bind and
UID1002-owned0700 ledger directory mounted ONLY in audio-device. Observer/app/transport
must NOT mount the ledger. Missing/corrupt state fails closed. `audio_qualification.issue`
is an explicit HOST-ROOT operation, never a boot/UI/MCP call. Seed the consumed r15
identity `f78a3c40135e445bbb1ba3eefca96d69` with `consumed=True` in a NEW policy path;
do not issue an empty attempt during recovery. First admission fsyncs the original
monotonic start and consumed segment count before child launch. Restart/candidate/
namespace changes retain that deadline; boot-ID change is terminal, not a reset.
Records are retained; only an explicit later root-issued NEW attempt may arm a test.

Apply `compose.audio-standby.yaml` LAST for immediate silent discovery recovery.
Standby requires the consumed ledger, has no scalar readers/device mappings/policy/
locks/capabilities, denies prepare/delta/start and never creates capture/DSP children.
MA/official transport/observer and renewable kernel-qualified pairing discovery remain
running. Scalar expiry halts audio, clears tickets/baseline, then persists terminal
state OUTSIDE the state lock; it does not exit the worker or tear down the namespace.
The factory still uses Restart=no. Final scalar descriptor cleanup has one owner.
This is quiet discovery, NOT hardware activation or gain qualification.

PCM stays in memory/pipes. Each meter reads at most8192 bytes; transient buffers are
under64KiB plus less than one frame residual. Receipts contain only frame-indexed
8192-frame per-channel peaks, sums of squares, nonzero counts, forwarding-byte counts,
PID/format/rate and monotonic read times. Eight output logs capped8MiB each and eight
input logs capped4MiB each total at most96MiB (budget exhaustion fails closed).
Forwarding-byte rows are coalesced at the same8192-frame interval plus final total;
every PCM byte is still immediately forwarded, first nonzero frame/read time retained.
The conservative300-second row-size test uses less than half each unchanged cap.
Do not save provider audio, payload stdout, HAR bodies or raw debug streams. Camilla
stdout is discarded; scalar-input stdout is exclusively the DSP pipe.

The graph remains full-range OUT3/4 at fixed−6dB, ten spare outputs zero. Matched
input/output scalar windows qualify that graph only; they do not establish the
earlier receiver/player volume stages. FIFO read time is NOT production time.
U1-03 additionally needs actual current-session ACTIVE/auth receiver level, identified
Pulse sink AND sink-input raw gains/mutes, actual Squeezelite applied attenuation,
and their ordering relative to first nonzero PCM. MA logical percent or command
completion is insufficient. Do not silently treat unity/default100 as settled.
Idle PipeWire sink metadata does not prove the absent Squeezelite stream's gain.

Persistent `PairingAdvertisements` records actual SRV/address record remaining TTLs.
Admit only cached unexpired matching-address ports continuously requalified against
the exact receiver binary/UID/namespace/birth/socket owner. Expiry/removal closes
pairing ingress; receiver owner replacement revokes the generation. No guessed TTL,
synthetic advertisement or private WS/control-port broad ingress is allowed.

Only after reviewed no-device generation, reader readiness, real authentication,
pairing discovery and scalar-only stage probes are established may the parent ask
for ONE genuine phone Spotify Play, explicitly silent. Agents send no playback,
seek, volume, unmute or grant commands. Phone discovery and first-PCM/gain proof
remain live acceptance checks, not results of the synthetic container gates.
