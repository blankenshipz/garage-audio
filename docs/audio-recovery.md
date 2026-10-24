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
SIGTERM/SIGINT cancel backoff and do not restart. Use **systemctl stop on the owner**,
not direct Docker manipulation: Docker does not distinguish an application that
exits 1 itself from one whose custom signal handler deliberately exits 1.

No recovery operation renews a grant, starts a qualification/recorder, calls Play,
seeks, restores a track, or forces volume. No credential bytes enter receipts.

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

## Deferred review follow-up (source only)

Cleanup now attempts every owned-helper stop, generation revocation, peer cleanup
and lock release even when an earlier helper times out. Timed-out owned children
receive a bounded kill/reap attempt; the initiating timeout remains terminal and
cannot authorize a replacement generation. An unsuccessful close can be retried.

The private `recovery-state.json` diagnostic has schema 1, a fresh owner invocation
ID, current admitted generation (null during startup), monotonic observation time,
retry count, and starting/ready/backoff/stopped/terminal/exhausted state. Ready means
the existing startup policy/OFF checks and stable receiver ownership admission
completed; it does not prove Spotify authentication, phone discovery or listening.
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

A deterministic virtual-clock characterization confirms that a successful Docker
inspection can exceed the independent guardian's two-second freshness budget
(the Docker wrapper permits up to four seconds). That budget and all authority
expiry rules are unchanged. A future inspection-budget redesign must fit complete
ownership/lease checks within the existing bound; extending authority or publishing
healthy ticks before complete checks is not this fix.

Focused tests: `python3 -B -m unittest discover -s tests -p audio_review_fixes.py -v`,
`python3 -B -m unittest discover -s tests -p audio_recovery.py -v`,
`python3 -B -m unittest discover -s tests -p audio_readiness.py -v`, and
`node --test web/tests/speakers.test.js`. These use synthetic process/IPC/time seams
and UI fixtures, not live Docker, authentication, devices or audio.

This follow-up additionally changes `audio_container.py`, `audio_public.py` and
`web/speakers.js`; the earlier five-file host-only deployment procedure does not
install these worker/dashboard changes. Coordinate a separately frozen release,
matching worker/dashboard assets, test environment and rollback with the active
owner. No deployment, merge or playback acceptance is implied by these source tests.
