# Garage audio lifecycle and bounded recovery

## Everyday journey

Start the existing `garage-audio-network.service` owner; it creates a fresh private
generation and advertises Garage Speakers. A human selects it in Spotify and
chooses Play. The observer requires a fresh authenticated/active playback delta,
current source corroboration and a one-use worker ticket; a status snapshot alone
cannot open the DAC. Public Stop/Pause silences output. Owner stop shuts down the
whole generation. Reconnect creates a new observer epoch, not a restored ticket.
Discovery, authenticated receiver state, DAC state and physical listening are
separate evidence. Discovery is not a playback acceptance test.

## What recovers

`scripts/audio-lifecycle.py` preserves the inspected external-normal-r20 driver
startup: immutable Boot inputs/images and exact hardware mapping; start MA and
dashboard with transport stopped; admit supported retained-provider policy; prove
ordinary hardware worker OFF with no children, open device or qualification;
then start transport/observer and prove stable pinned receiver/listeners.

The guardian records the initiating failure **before** stopping siblings. Only
an exact registered same-generation dashboard, observer or transport container
exiting with an application code 1–125 qualifies. It first checks carrier,
lease, receiver and every other dependent's identity/birth/namespace. Receiver/MA
or device-worker loss, namespace/identity changes, permission denial, unknown
exceptions, OOM, signal exits, consent/recording expiry and lease loss are terminal.
Neither a later exit 137 from guardian cleanup nor a generic failed systemd unit
is evidence of a recoverable crash. Startup absence gets a bounded 60-second
admission window; ambiguity/permission denial does not.

The owner holds a private nonblocking lifecycle flock across cleanup/backoff;
Carrier retains its existing independent generation flock. After complete
cleanup, every pinned prior container must be stopped and the hardware-worker
closure acknowledgement must be OFF/no children for that generation. Failure
of cleanup or its proof prevents replacement. Inputs are revalidated each time.
At most three retries per owner invocation: 5, 30, 120 seconds, at most three in
600 seconds. The lifetime cap never resets after a quiet period. Exhaustion exits
nonzero and requires an explicit owner start; systemd `Restart=no` is retained.
Pre-admission infrastructure failures (carrier namespace, macvlan, DHCP or Docker
not ready, before any dependent is registered and with no ownership/permission error
in the chain) use the same bounded retries; on exhaustion the owner exits 75
(EX_TEMPFAIL), which the unit retries via `RestartForceExitStatus=75` under a start
limit. Exit 1 is never restarted. The carrier waits (bounded) for a just-stopped prior
generation's namespace to release the dedicated MAC instead of failing on
`RTNETLINK: Address already in use`; a foreign MAC holder still fails.
Transient resolver or connection-startup failures use the same three bounded retries and
the same exhaustion exit 75, even after dependents (Music Assistant itself) are registered;
teardown is proven closed before any retry. Recognised: `socket.gaierror`
(`EAI_AGAIN`, `EAI_NONAME`, `EAI_NODATA`; a degraded local resolver stub returns them
interchangeably, and a truly wrong hostname still ends after the 3 retries), connection
refused/reset, wrappers that carry them (`__cause__`/`__context__`, aiohttp `os_error`,
`URLError.reason`, exception groups), stringified forms ("Temporary failure in name
resolution", "Name or service not known", "No address associated with hostname",
"Connection refused", "Connection reset by peer"), and the exception class the policy
script reports across `docker exec` (`ClientConnectorDNSError`, `ClientConnectorError`,
`ServerDisconnectedError`, ...). A bare timeout, any permission error in the chain, other
`OSError`/`ValueError` and every unknown error stay terminal.

The transport container removes a stale PipeWire socket and lock left on the persistent
runtime volume before starting PipeWire, waits for this PipeWire's own socket, then sets
group 1003 / mode 0660 and verifies it (socket, group, mode); a mismatch or timeout exits
nonzero. PipeWire 0.3.65 has no verified config option for the native socket mode/group,
so the post-start chgrp/chmod plus verification is used. If a capture child (pw-record)
exits within the first 0.5 s of a segment, the worker halts with reason
`capture_connect_failed` (shown in `audio.status` and the observer decision trace as
`ipc_error`) instead of reporting `playing`.
needrestart must not restart the owner (`config/system/needrestart-garage-audio.conf`):
on 2026-11-13 a libc upgrade restarted it and the restart lost that race.
SIGTERM/SIGINT cancel backoff and do not restart. Use **systemctl stop on the owner**,
not direct Docker manipulation: Docker does not distinguish an application that
exits 1 itself from one whose custom signal handler deliberately exits 1.

No recovery operation renews a grant, starts a qualification/recorder, calls Play,
seeks, restores a track, or forces volume. No credential bytes enter receipts.

## Heartbeat tolerance and stall recovery

The independent guardian process publishes `guard-heartbeat.json` after each full
ownership pass; the owner's watcher compares its age against a tolerance.

- **Guardian exited** (the process is gone): immediate revocation, reason
  `independent_guard_lost`. Terminal, as before.
- **Guardian alive but heartbeat older than the tolerance**: the generation is still
  revoked and every owned container stopped (muted/fail-closed), but the failure
  is classified `guard_heartbeat_stall` in `guard-failure.json` with the measured
  staleness and the tolerance. This kind is **recoverable** through the existing
  bounded Supervisor (5/30/120 s, at most three in 600 s, lifetime cap, then
  `exhausted`); no new delays or caps exist. Before any new generation the usual
  `verify_closed()` proof must succeed, so a stop that cannot be proven fails closed
  and is terminal.
- The stalled guardian is terminated (bounded wait, then killed) so it cannot outlive
  the generation. If it cannot be proven gone the failure is terminal.
- The watcher stops the guardian before it writes the stall, and the lifecycle driver
  can see the guardian gone first. The driver therefore waits (bounded by the terminate
  and kill waits plus a margin) for the watcher's final verdict before classifying the
  failure, and classifies it terminal if no verdict arrives in time. A heartbeat that is
  missing, torn or malformed counts as missing (staleness runs from the last good value
  or the watcher start); any unexpected watcher error is a terminal revocation.
- The stall is written once. It is never written over an existing terminal
  classification, `guard-revoked.json` or `authority/revoked.json`, and a late write
  by the stalled guardian cannot replace it. Owner/DHCP/receiver loss, lease or
  address change, namespace or generation change, ownership mismatch,
  permission/OS/value errors, a deliberate stop and an existing revocation remain
  terminal.

`GARAGE_AUDIO_GUARD_HEARTBEAT_TOLERANCE` sets the tolerance in seconds (a number
from 10 to 300, default **12**). It is read once when the carrier is constructed; an
unparsable, non-finite or out-of-range value stops startup with an error that does not
echo the value, rather than falling back to a weaker setting. The earlier fixed 2 s
was shorter than one guard pass can legitimately take: each pass makes one batched
`docker inspect` (own 6 s timeout) plus a namespace probe (2 s), and a pass that times
out publishes nothing. The minimum of 10 s is above that 8 s worst-case successful pass,
so a healthy but slow pass can never be classified as a stall. 12 s allows one such slow pass plus scheduling delay under
memory/swap pressure while a hung guardian is still detected within seconds. Do not raise it to hide a persistent fault;
prefer `config/system/garage-audio-network.service.d-resource-protection.conf.example`
(copy by hand; nothing installs it) to keep the units resident and scheduled.

Inspect state without side effects:

```
python3 -B -m garage_audio.audio_recovery status [--root PATH] [--json]
```

It only reads: it takes no lock, writes nothing, never creates the root, and exits 2
if the root is missing or unreadable. It reports the generation, recovery state,
retry count, the effective heartbeat tolerance as read from the status process's own
environment (labelled as such; an invalid value is reported as invalid, never echoed; the
owner unit's `Environment=` may differ), the last guard failure (kind, staleness,
tolerance), last revocation
reason, driver failure and heartbeat age (`time.monotonic`, system-wide on Linux; an
age from another boot is shown as unknown), plus a plain explanation of each state:

| state | meaning | operator action |
| --- | --- | --- |
| unknown | no `recovery-state.json` under the root (never ran, or removed) | check the service manager first |
| starting / ready | a generation is being admitted / admitted (not playback proof) | none |
| backoff | a recoverable failure; the Supervisor will retry on its own | wait; do not restart |
| stopped | deliberate stop | start the owner when wanted |
| terminal | a security or ownership failure or unrecoverable cleanup | find the cause first; then `systemctl start` the owner |
| exhausted | recoverable failures hit the retry or lifetime cap | fix the underlying load/fault, then `systemctl start` the owner |

Restart only with `systemctl stop`/`start` on the owner, never by manipulating
containers directly. Terminal and exhausted exits require an explicit start.

## Startup sound risk: important activation prerequisite

This is ordinary **hardware** mode, not a silent qualification. Fresh worker OFF
and muted/-90 DSP startup are real safeguards but **not a persistent mute**:
`audio_container.open_muted_device` verifies initial mute then explicitly unmutes
to the fixed unity ceiling once a ticket qualifies. `audio_observer.ReceiverState.accept`
creates a fresh ticket on authenticated `playback_changed: playing`, including a
provider-originated delta after reconnect. It cannot distinguish human Play from
an autonomous provider resume delta. A snapshot does not suffice; an actual delta
plus active/auth/MA corroboration can. Thus there is no forced Play/record call,
but retained Spotify/provider state can produce audible resume after startup.
Public Stop is silence-only, not a durable startup inhibition; changing saved
volume can be superseded by Spotify-authoritative volume and is not a proof of
silence. Do not claim ordinary startup guarantees inaudibility.

Before activating while audible playback is forbidden, establish a physical
speaker/amplifier mute or disconnection that cannot be undone by this software,
or obtain explicit acceptance of this precise provider-resume risk. Do not weaken
ownership checks, change permissions, replace hardware mode or invent credentials
to get past this prerequisite. No recording is needed for discovery checks.

## Frozen deployment and rollback (host administrator only)

An existing safe alternative is `compose.audio-standby.yaml` applied LAST. It
removes hardware mappings/policy and denies prepare/delta/start, requires an
existing consumed terminal ledger (never issue a fresh attempt), and creates no
scalar readers, capture or DSP children. The lifecycle preserves the inspected
external-r18 no-device and terminal-standby status checks before transport/observer
start. This supports discovery even if the provider emits resume deltas; it does
not enable ordinary listening. Keep the normal retained transport/image inputs,
and supply only the existing consumed policy/store credential-free references in
a frozen environment file. Never use scalar mode as a discovery workaround.

Pause source writers. Freeze a committed SHA and SHA256 inventory of exactly:
`garage_audio/audio_receiver.py`, `garage_audio/audio_network.py`,
`garage_audio/audio_recovery.py`, `scripts/audio-lifecycle.py`,
`scripts/audio-lifecycle-policy.py`. Keep all receipts **outside** the candidate.
Retain copies/hashes of the current installed receiver/network modules and systemd
unit, and the existing external-normal-r20 driver. Reuse installed unaffected
modules, images, Compose inputs, device policy, retained provider state and secret
references. Do not rebuild or deploy unrelated calibration/UI sources.

On garage-server, require sufficient disk capacity, no competing lifecycle and
the sound-risk prerequisite above. Stop only `garage-audio-network.service` (and
its lifecycle-owned enrollment helpers); verify prior containers stopped. Stage
the three host modules into `/usr/local/libexec/garage-audio-network/garage_audio/`
atomically, root-owned 0644. Stage the two scripts into a root-owned immutable
release directory. A unit drop-in changes **only** ExecStart to:

```
/usr/bin/python3 -B /opt/garage-audio/releases/<committed-sha>/audio-lifecycle.py
```

Retain WorkingDirectory/PYTHONPATH, `Restart=no`, logs and the existing unit
ownership. Allow enough graceful stop time for existing bounded guardian cleanup
and enrollment stop. Reload the unit and start only that owner. No blanket
container restart policy, Docker pull, host reboot or other workload change.
Verify installed hashes against the frozen inventory, unit state, new boot/input
and hardware-preadmission receipts, stable pinned receiver/listener and current
LAN `_spotify-connect._tcp` advertisement. Query private receiver authentication
using fixed nonsecret projections only; never dump its WS responses/credentials.
Verify current worker OFF/no children/no open device/no qualification and do not
issue Play or start recording. Phone visibility and physical silence remain
separate, unproven unless actually observed.

Rollback: stop the same owner; verify its generation closed; restore the retained
module bytes and unit/drop-in, reload systemd. Start the old owner only under the
same sound-risk prerequisite. A rollback can restore the original scanner fault;
do not repeatedly restart a terminal security failure. Preserve failed receipts.

## Validation

`python3 -B -m unittest discover -s tests -p audio_recovery.py -v` exercises real
supervisor/entrypoint code with synthetic Docker, systemd and policy responses:
cleanup ordering, policy-before-transport, OFF-before-observer, classified retry,
permission failure, intentional stop, cleanup failure, backoff/caps/exhaustion,
duplicate-owner lock and no Play/record calls. This is not live crash injection,
rendered UI, authentication, physical audio, or provider-resume proof.
`tests/audio_receiver_scan.py` uses isolated Linux UID1000 real sockets and
controlled proc errors, with no audio devices or network access. Retain the
expected old-source reproduction failure as well as the patched result.

## Reviewed follow-up (source only, not activated)

Cleanup now attempts every owned-helper stop, generation revocation, peer cleanup
and lock release even when an earlier helper times out. Timed-out owned children
receive a bounded kill/reap attempt; the initiating timeout remains terminal and
cannot authorize a replacement generation. An unsuccessful close can be retried.

The private `recovery-state.json` diagnostic has schema 1, a fresh owner invocation
ID, current admitted generation (null during startup), monotonic observation time,
retry count, and starting/ready/backoff/stopped/terminal/exhausted state. Ready means
the existing startup policy/OFF checks, stable receiver ownership admission and
nonempty verified pairing-listener admission completed; it does not prove Spotify
authentication, phone discovery or listening.
Terminal generation exceptions retain counts without including exception messages.
This receipt is never playback authority.

Public status/Stop/Pause now accepts a constrained standby readback: OFF, no device,
no children or scalar readers, consumed terminal ledger, and no physical success
claim. Qualification attempt identifiers remain private. Worker readiness is a
small enum derived from existing network validity and fresh observer metadata;
its separate metadata timestamp never renews playback authority or mints a ticket.
The dashboard distinguishes standby, missing observation/auth/active device,
waiting for fresh Play, waiting for source corroboration, and software playing or
paused. Phone visibility and audibility remain unverified. Fosi first-play volume
guidance is also shown in the Docker view.

A deterministic virtual-clock characterization confirmed that a successful Docker
inspection could exceed the earlier fixed two-second guardian-heartbeat budget.
That finding is addressed by "Heartbeat tolerance and stall recovery" below; the
guardian's authority expiry rules and complete ownership/lease checks are unchanged
and a healthy tick is still never published before those checks complete.

Focused tests: `python3 -B -m unittest discover -s tests -p audio_guard_heartbeat.py -v`
(heartbeat tolerance, stall classification/recovery, batched inspect and status; fakes only),
`python3 -B -m unittest discover -s tests -p audio_review_fixes.py -v`,
`python3 -B -m unittest discover -s tests -p audio_recovery.py -v`,
`python3 -B -m unittest discover -s tests -p audio_readiness.py -v`, and
`node --test web/tests/speakers.test.js`. These use synthetic process/IPC/time seams
and UI fixtures, not live Docker, authentication, devices or audio.

### Canonical disposition (R1–R5)

- **R1 accepted with correction:** complete owned-helper cleanup despite timeouts.
  Revocation must prove all owned containers stopped and, when required, software
  children closed; merely writing a revocation receipt is not successful cleanup.
- **R2 accepted with correction:** private recovery transitions and diagnostics;
  publish ready only after receiver and pairing admission, never merely startup.
- **R3 accepted with correction:** constrained standby/public readiness projection.
  Rejected events never refresh readiness metadata; slow accepted events retain
  receipt time, not completion time. Metadata never renews playback authority.
- **R4 wording accepted, visual acceptance limited:** standby/readiness and Fosi
  guidance rendered in a local browser fixture at 1440×1000 and 390×844 for standby,
  waiting-for-Play and playing. Keyboard focus/Tab, native anchor and reload were
  exercised. Narrow sticky navigation overlaps upper content on focus-scroll;
  whether this predates the changed lines is not established. Full-app selected
  navigation, authentication/reconnect, phone and hardware remain unverified.
  This is not a clean narrow-screen visual pass or a live playback claim.
- **R5 deferred:** deterministic inspection-budget characterization only. No live
  timing fault is established and no guardian freshness/authority boundary changed.

Supported isolated Linux validation used Python 3.13.16 and Agents SDK 0.23.1:
52/52 application tests passed, zero skips. The subsequently corrected FILE/HTTP
IPC status/Stop/Pause journey passed separately. Frontend tests passed 84/84 with
zero skips. These are software/fixture results, not production or physical proof.
Private full logs, source inventories, failed attempts and browser screenshots
are retained outside the repository in the integration/check receipts. The stale
configuration-operation IPC expectation was corrected rather than weakening the
production schema. No inference, playback or recording was performed.

### Matching activation and rollback prerequisite

Source integration is separate from deployed `64440af`. This follow-up additionally
changes `audio_container.py`, `audio_public.py` and `web/speakers.js`; the earlier
five-file host-only procedure **cannot install the complete feature**. No live
activation is authorized by this review or its source tests.

Before a separately authorized activation, the existing owner must establish
actual playback state and the sound-risk safe point above. Pause writers and
freeze the committed source SHA, exact host module/script inventory, worker image
digest, dashboard image/assets digest and matching Compose/configuration inputs.
Retain known-good installed host bytes/unit and worker/dashboard image references
with their matching configuration for rollback; keep private state, credentials
and transient receipts outside shipped inputs. Preserve credential references,
device policy, ownership and consent boundaries, and unrelated workloads.

Install host, worker and dashboard as one compatible frozen release using the
existing lifecycle owner, not a host-only upgrade. Verify installed hashes/digests
and the actual public status/Stop/Pause boundary, standby versus hardware mode,
receiver/pairing admission and freshness/readiness projection. Rediscover supported
operations on reconnect; never replay old sound authority. Only user-originated
Spotify playback can establish the ordinary listening journey and audible result;
software playing/discovery alone cannot. No agent-forced Play is part of this work.

Rollback must stop the same owner and prove its generation closed before restoring
the retained matching host/unit, worker and dashboard inputs. Preserve failures
and private state; restart the old owner only under the same safe-point prerequisite.
Do not mix old worker/public schemas with new dashboard assets or erase retained
provider state. Activation, rollback execution and physical listening remain unrun.
