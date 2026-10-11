# Music-first dashboard and persistent MA transport (intent 16 candidate)

Intent17 candidate: the main card directs normal MA listening through **Garage
Speakers control** with explicit chosen duration/ceiling. The optional diagnostic
check is separate. Actual service-reference readiness, native mode/transport/
remaining deadline/ceiling and upstream MA volume are distinct from saved Spotify
configuration. See `normal-listening.md`; normal candidate is not yet deployed.

Phone dashboard opens immediately with actual saved Spotify configuration, observed
Garage Speakers connectivity, and speaker-output state shown separately. **Open
Music Assistant** is the music/source/Play/volume workflow. No displayed code or
Setup/Enable gate. Spotify saved configuration is not verified OAuth/music delivery.
**Stop speaker output** closes the DAC, not necessarily MA's playing queue.

First-use check is explicitly separate from ordinary listening: prepare the quiet
zero-output graph, then a genuinely attended fixed OUT3/4/-60dB/120s check. The
code-free MA-human approval adapter is implemented as a staged provider/private
Unix bridge; see `ma-attended-check.md` for its exact review/deployment scope.
It is not production-deployed. The dashboard audible-check button stays disabled;
when the private bridge is present it points to the explicit MA provider Options
actions rather than authorizing sound itself. Existing
CLI/native browser authorities remain internal compatibility paths; no agent pairs
a new browser, impersonates MA user, or relabels a LAN session as human.

The real Squeezelite player now depends only on the project's no-hardware PipeWire
graph. Native DAC/producer expiry or Stop does not stop MA registration. Native
check preparation (phone and CLI fallback) shares one barrier: starts the transport
if needed, observes exact registration,
then uses supported Slimproto stop + stopped-mode readback (or the installed
server's exact connected, powered-off, no-pending-play projection) before any capture
reader. This stops pending transport audio without clearing the MA queue; no auto
play/resume follows a check. MA software volume only attenuates upstream PCM and
does not change the native master ceiling. Outside a check, DAC remains closed.
No ordinary unlimited listening policy is claimed by this two-minute check.

The existing HTTP trusted-home-LAN transport does not defend against LAN
interception/modification. Do not represent it as HTTPS or confidential transport.
No password is collected. The separate HttpOnly SameSite=Strict cookie is scoped
to `/api/speakers`; cookies ignore ports, so duplicate names/headers fail closed.
No dashboard token, unlock screen or pair-to-view gate on the explicitly configured
LAN authority. Ordinary access is automation-level and automatically provisions an
HttpOnly session for CSRF; it cannot pair or grant listening authority. Other accepted
authorities and default configuration retain token authentication/MCP compatibility.
Anyone on the trusted LAN can access ordinary room/photo/control data. Exact Host,
Origin, cross-site Fetch Metadata rejection, bounded requests/session admission and
CSRF remain. No `/api/phone` enrollment or claim endpoints are shipped.
Root memory stores hashed speaker possession/CSRF/action values, expires sessions
after 600 seconds, and loses every pairing/action on restart. Public 12-hex codes are
pending identifiers, never bearer grants. Only an actually user-returned code
authorizes root pairing; agents must never pair their own production browser.

Root-only `speaker-entry.py pair CODE` uses `/run/garage-speakers/pair.sock` mode
0600. This is not mounted into the dashboard. The distinct web control socket
is `/run/garage-speakers/web/control.sock`, root:garage-control 0660, admitting
only the configured observed nonroot dashboard Unix UID. Root configuration
`/etc/garage-audio/speaker-web.json` is immutable and contains only `web_uid`.
Current dashboard UID is 1000; garage-control GID must be checked as 1003 before
using the host-specific Compose group entry. Do not add audio/DAC groups.

The controller system service is installed **disabled**; service startup creates
IPC only, not DSP/player startup. It has no TCP listener. It is a new explicit
native peer identity (`python -I speaker-entry.py serve`), not forged CLI args,
roles, event.isTrusted, agent bearer, FILE human grants or old `/api/human`.

Setup launches only the project DSP and player services. Native initialization
uses the verified real hardware configuration with a route mapping containing
**no destinations** and every branch gain muted, plus master mute/−60 dB.
No fake/null production player or raw ALSA shortcut. Producer initialization
must prove its zero-graph hash. BEFORE rearm/producer startup, `phone-start`
establishes the independent controller pidfd/120-second deadline guardian on the
muted startup DSP. No sound grant or timer renewal occurs. Readiness polls for
two stable zero-graph generation samples with cancellation and a 20-second bound;
`phone-prepare` requires that same early owner and does not reset its deadline.
Source/DSP readback
failure terminates the owned generation/capture, not independent MA transport.
MA registration is separate from native
player startup. The controller queries the supported local Slimproto JSON-RPC
`players` inventory, bounded to 100 entries/32 KiB, matching fixed MAC
`02:00:00:00:21:01`, name Garage Speakers and connected status. It does not read
MA passwords/tokens/provider state. Missing/unavailable inventory disables Enable.
The UI reports registration only from that real inventory, not TCP health.

Enable requires paired possession, separate CSRF, a fresh 30-second one-use
action and exact prepared generation. The broker requires the same admitted
controller connection and the unchanged zero-graph hash, loads the deterministic
stereo graph while muted, verifies readback, then creates the fixed OUT3/4-only
−60 dB / 120-second lease and unmutes. No caller-supplied duration/gain/output.
Independent native guardians stop on deadline/controller death/binding failure.
Ordinary automation cannot pair or enable; authenticated automation can STOP.

Stop uses an independent native connection and command outside DSP/file locks;
only the owned DSP is killed. Exit is verified. During in-flight startup the UI
reports `stopping`, never falsely acknowledges `stopped` before cancelled worker
completion. A shutdown-count barrier rejects Setup until ALL concurrent Stop and
worker-finalization work completes; failed Stop remains `stop_unverified`.
Native/control restart invalidates all actions and never resumes.
Dashboard logout revokes its associated speaker browser/actions first, and stops
only a window/setup owned by that browser. Other-browser logout does not stop the
wrong owner. Successful logout clears both cookies. If native revocation/exit
cannot be verified, ordinary logout is rejected and the dashboard session remains
available for Stop (rather than hiding Stop while the window may still exist).
LAN mode hides the ordinary Lock button because access is intentionally available.
The backend logout API still revokes associated speaker possession/window, clears
ordinary/speaker cookies and preserves existing failure/Stop semantics. A subsequent
LAN visit obtains only a fresh unpaired automation session, never sound authority.
Existing CLI helper remains a separate fallback authority; old web/MCP human
paths remain denied on the LAN/container dashboard.

## Acceptance evidence and remaining work

Current work is a **local candidate**, not deployed acceptance. Isolated Chromium
at 390/1280 widths exercised the real HTTP handler and existing page with a
synthetic native fixture: setup, public code, isolated pairing, enable, stop,
reload. Real Camilla FILE packed24/12 render separately proved all-zero setup
and only 3/4 at low level on enable; neither proves physical audibility.
Checkpoints/failed receipts stay in private state, not this shipped document.
Targeted native guardian/HTTP boundary checks and candidate review precede
privileged deployment. Production pairing/Enable remain genuine user actions.
Prior deployed zero-output Setup/real MA registration/Stop has separate retained
evidence. This new no-lock LAN candidate still requires reviewed deployment;
genuine phone approval and physical listening remain unverified. No Spotify/provider
data, MA restart or scanner change is involved.
