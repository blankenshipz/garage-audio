# Garage Speakers control — manual fixed stereo line design (intent 18)

**Historical manual-listening design, not the everyday Spotify entry.** Later
playback work records deployed Spotify Connect source set `b81fbf71` on October 11,
2026. Ordinary Spotify playback uses [Spotify only](spotify-only-quickstart.md),
without this document's MA Start listening button. Physical-phone cold-start,
audibility and Pause/Resume/Stop timeline acceptance remains separately pending.
The source review has not independently rechecked or changed production.

The original intent18 candidate described below extends the
existing `garage_speaker_control` provider, retaining its instance and MA accounts,
provider/queue configuration and authentic session boundary. No vendor core fork,
second login system, pairing codes, dashboard lock or acoustic prerequisite.

## Phone journey

Open Music Assistant at http://192.168.68.74:8095 using the existing MA sign-in.
In Settings → Plugins, add **Garage Speakers control** if not already listed,
then open it. Choose 1/2/4 hours (default 2). Fosi's physical knob is the primary
volume control. The new line profile has unity master gain and a single −6 dB
headroom stage, full-range separate L/R on OUT3/4, no EQ, inversion, delay, mono
sum or crossover, and no sub output. Turn Fosi fully down before fresh Start.
This is 20 dB higher than the old −20 master at equal MA volume; old approvals
and cached Start actions do not authorize it. Digital gain is not verified safe SPL.

**Start listening** silently prepares the real zero-output graph, stops pending MA
transport audio without clearing its queue, sets and verifies MA volume at 10%, then
binds this consent to the exact current native generation/device/graph/boot. It does
not start a song. The user selects Garage Speakers and their source, presses Play,
and uses normal MA volume, Pause, Resume and Stop. MA volume remains upstream
attenuation; it cannot exceed the fixed unity master. Its percent-to-gain
mapping is not assumed linear or equated to a native dB setting.

Pause promptly mutes the native output and holds the muted session at most ten
minutes. Resume retains the actual user-requested MA volume, not a cached louder
value; the native gate settles muted at −90 dB and fades up over 400 ms under the
fixed unity master. Stop/off/idle after first Play closes DAC/capture and ends consent,
while persistent MA registration and the queue remain. Before first Play, a newly
approved session may wait quietly at most ten minutes. Unknown transport fails
closed; idle is never guessed to mean Pause. Explicit Stop output is always available
at http://192.168.68.74:8098 and does not require new consent.

The optional first-speaker-check actions remain separate: −60 dB, 120 seconds,
OUT3/4 only. They are not a recurring normal Play gate or acoustic verification.
Login expiry/logout/revocation, service failure, native deadline and restart may
end a session sooner than the selected maximum. No sound, gain or consent is restored
after restart. Start failures/cancellation never preserve a hidden grant for retry.

## Enforcement

The MA provider revalidates canonical current-user/token/session metadata and
CONFIG_PROVIDERS_WRITE inside MA. Null/guest/service/unknown/impersonated/revoked/
expired/long-lived integration sessions cannot approve output. No bearer token is
exported. Scope proposals are keyed by real session fingerprint; a stale Start button
cannot silently authorize a newly changed scope. Stop only reduces authority.

The root-private Unix bridge checks kernel peer PID/UID, pidfd, boot/starttime,
mount namespace, executable/argv hashes and immutable provider files. Exact private
process UID is1000, matching the existing production MA service, not controllerroot.
Its directory is root-owned0711 (traverse-only to the readonly MA mount, hostparent
root0700), socket UID1000:1000 mode0600. UID alone never grants admission: unrelated
sameUID and UID0 processes are denied by exact process pinning. The service pin,
reference and immutable provider assets remain root-owned; MA has no write access.
normal messages are `normal-prepare(session,hours,policy="fosi-line-v1")`,
`normal-heartbeat(session,state,volume)`, and
`normal-confirm(session,handle,generation)`. The handle is an internal one-use
session/generation/scope HMAC, never a browser code. Diagnostic messages and native
broker commands remain distinct; caller role/gain/duration passthrough is forbidden.

Normal scope is accepted only from the installed root phone controller, not CLI,
dashboard/MCP or generic UID0 processes. Native broker enums validate hours1/2/4 and
the exact versioned line policy, not a caller's arbitrary gain or old ceiling.
The single −6 dB headroom/flat stereo graph routes only OUT3/4, with the other
ten packed24 outputs zero. Native clipped-sample growth independently stops DSP,
not a dynamic limiter or calibrated acoustic protector. The fixed flat path's
gain stays below unity for normalized inputs; upstream clipping cannot be repaired.
Native lease binds boot/generation/
hardware/capability/graph and has an immutable selected absolute monotonic deadline.
One-second MA server auth/state observations maintain liveness, not duration. An
independent native ten-second watchdog and pidfd/deadline guardian kill owned DSP
before waiting for locks/websocket/systemd. A stalled readback cannot renew authority.
Pause/initial-idle also have independently checked ten-minute maxima.

## Root-only MA restart-reference refresh

`ma_reference.py` polls exactly `garage-audio-music-assistant-1` via the existing root
administrative Docker trust boundary. Production accepts only projectgarage-audio,
servicemusic-assistant/container1, working directory/home/zach/garage-audio, the
existing pinned MA2.10.5 image28023f8c, exact existing MA UID1000:1000 and
Python3.14 executable/argv hashes, unchanged
MA data bind and exact read-only provider/socket binds. Source and metadata checks
never accept actor/header/target strings from HTTP/MA clients. An internal explicit
isolated-scope dependency is used only by FILE fixtures, not the production observer.

The installer provisions `/etc/garage-audio/ma-service.json`, root0600, containing
only image/executable/argv/provider hashes (`image`, `exe`, `exe_sha256`,
`argv_sha256`, `assets`). No credentials. Missing/invalid service pin makes normal
listening unavailable. Process drift/death first ends old output and pending consent;
only after verified native exit does the root helper atomically publish a new private
`ma-peer.json` and replace admission. No automatic preparation, login or approval.
Invalid image/project/mount/process/provider leaves readiness false and output closed.
Current-service readiness is separate from saved provider/source configuration.

## Packaging/deployment gate

Freeze native/provider/UI/transport inputs and required docs before release builds.
Preserve exact installed official binary/library/SDK wheels and existing root unit,
DAC permissions and no-hardware PW/player services. Use root-private rollback and
byte-verified data snapshots; never log MA auth values. Reconcile against the **actual
deployed** MA Compose including both existing readonly bridge/provider mounts, not
the stale local Compose. Preserve MA users/provider/queues since last deployment.
Only named MA/controller/dashboard recreation may be needed; no upgrade/new ports,
unrelated scanner restart or global device permission changes.

After targeted review: install exact candidate bytes plus private service pin,
recreate only MA on unchanged image if required, verify current metadata, start only
the controller/ref observer, and inspect live readiness with DAC closed. No agent
may invoke production Start listening/diagnostic confirmation, play physical music,
increase live gain, fabricate presence or log in as the user. Actual Spotify account
setup remains user-owned through supported MA UI; saved configuration alone is not
OAuth/music readiness. Root-owned physical phone/listening acceptance remains separate.
