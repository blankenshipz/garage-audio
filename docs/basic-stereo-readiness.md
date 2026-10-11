# Basic stereo listening preparation — intent revision2

No microphone, sweeps, room correction or acoustic measurement is required for
basic speaker listening. Actual low-gain presence approval, trusted sound policy
and dedicated DAC provisioning are still required. Nothing in this preparation
starts a PCM stream, service or music queue, grants sound authority or accepts
Spotify terms/login on the user's behalf.

## Actual readiness (2026-10-10 UTC)

- UMC still advertises12 playback/S24_3LE,10 capture/S32_LE and48k; both stream
  statuses remain closed. Existing serial/CARD binding is unchanged.
- Native garage-audio-dsp unit/account and user player/PipeWire graph are absent
  or inactive. Host `pipewire`, `wireplumber`, `pw-record`, `squeezelite` commands
  are absent; `Linger=no`. Prior project-private test binaries are not installed
  system services. `sudo -n true` returned `interactive authentication is required`;
  no privilege operation was attempted through Docker or another path.
- MA2.10.5 `/info` reports schema65 and **onboard_done:false**. UI `/` returns200.
  Its ports8095/8097/3483 are loopback-published on an isolated Docker bridge.
  No private `secrets/ma-token` reference exists. Authenticated provider/player
  counts were not queried or guessed; queue/provider state was not changed.
- Real official MCP client initialized the deployed stdio server (34 tools,
  five resources), then status/capabilities and shared HTTP readback agreed:
  DSP unavailable, playback credential not configured, attended authority false,
  human approval unavailable. `playback.status` returned
  `ma_credential_required`. No play/unmute/clear operation was invoked.
- The deployed wildcard/container dashboard cannot accept human credentials;
  its existing human verifier/approval engine covers FILE/null jobs, not hardware
  playback. A localhost URL or tunnel does not promote it to a trusted approval
  service. Native raw Camilla websocket must stay private.

## Stereo data staged, not active

`profiles/basic-stereo.json` is a recognized existing profile-schema document:
no invented commissioning/calibration flags or new authority protocol. Only
left/right branches are intended to play; sub branch is muted, so OUT5 remains
silent. Fixed compiler map stays OUT3/4, all unused outputs unmapped. Existing
-6dB headroom, native ceiling-20 and LR4 80Hz main highpass are preserved; this is
an uncalibrated conservative first-listen profile, not a full-range/corrected
acoustic claim. Profile data does NOT include or establish runtime master mute.
The compiled staged candidate separately mutes all branches; eventual native
startup must also prove master muted/-60 before any approved listening profile
is applied. No installed active profile was replaced.

Server stage: `state/hardware-preparation/basic-stereo-20261010T023437.523160Z/`.
Its readback and schema-reuse receipts retain exact source/candidate identity.
Muted candidate bytes are identical to the previous Camilla-checked candidate;
the prior schema exit0 receipt is reused explicitly, not relabelled or rerun.
Fresh profile validation/compiler readback passed and both streams stayed closed.
Profile file SHA256:
`33afd6766872b3b2deb0c2e25840ca8dcd856b80c788c8f3a3839edd6ee81cee`.

The same recognized profile was saved through **real MCP** to the existing
dashboard's offline profile store, ID `81671c00a48f4e8fa0d0b0809020f2e8`.
MCP get and authenticated shared HTTP get returned identical data, sub muted and
verified_acoustically:false. It was not applied to native DSP and grants no sound
authority. This is a useful quiet agent-first setup checkpoint, not playback.

## Smallest supported journey and remaining implementation

1. **Phone on home Wi-Fi:** open `http://192.168.68.74:8095` directly.
   Intent5 supersedes the earlier SSH onboarding instruction; no Mac/SSH is
   required. User creates the MA account and signs into Spotify, not the agent.
   Only off-host HTTP and websocket connectivity were observed in this worker;
   browser rendering and the physical phone journey still await verification.
2. Review/install the freshly pinned native DAC-isolation bootstrap/bundle with
   actual serial/CARD/PCM and12/S24_3LE capability policy. Administrator reviews
   system package/linger prerequisites and dedicated groups; no broad audio-group
   grant. Do not run mutable project code with sudo. No service should be enabled
   for automatic playback or started while approval is pending.
3. Add a focused hardware-playback authority adapter to the existing policy
   boundary. Source scope: `human_authority.py`, `webserver.py`, `application.py`,
   `broker.py` and focused tests. Existing hardware play actions are unconditional
   `attended_authority_required`; FILE/null job grants cannot authorize them.
   Implement genuine independent human approval tied to exact stereo outputs,
   pinned capability/identity, DSP generation, bounded low gain/duration and
   revocation/deadline stop of the owned player/DSP. Keep the current container
   human routes denied and raw DSP private; do not accept agent presence strings
   or invent an enrollment/approval credential. This is a scoped security change
   requiring parent review before privileged/live activation, not a permanent
   code-missing prohibition.
4. Once installed and independently approved, start the isolated native graph
   muted, verify actual readback and exact device ownership, add MA
   Settings → Player Providers → Squeezelite (3483; discovery off; CLI/JSON-RPC0).
   Sole transport remains MA → Squeezelite → project PipeWire sink → protected
   FIFO/DSP → UMC3/4. No Sendspin/Snapcast/rawALSA replacement is needed.
5. User completes the attended bounded low-gain stereo check and confirms left/
   right audibility and stop. This is basic channel safety, not acoustic
   calibration; no microphone is required. Do not mark the entire2.1 physical
   map verified from a stereo-only check. Then user starts music through the
   supported approved path. MA/Spotify volume is not a substitute for DSP mute/
   ceiling. Squeezelite has no native mute safety guarantee.

## Spotify choice stays user-owned

Official MA docs were checked again; existing `docs/spotify.md` is accurate.

- Spotify library/playlists **inside MA**:
  Settings → Music Sources → Add a music source → Spotify. Premium is required
  (including Duo/Family); OAuth and one-time engine pairing are user actions.
- Control from the **Spotify app device picker**:
  Settings → Plugins → Add a plugin → Spotify Connect → choose engine → Connected
  players → Garage Speakers only. Same-LAN phone discovery needs live proof.
- Soloist supports new accounts, but creating its private key initially requires
  Premium and user terms/download consent. Later Connect users may be Free or
  Premium. User enters the key only into MA; never into chat or agent logs.
- Community librespot/go-librespot targets Premium accounts created before
  December2024; do not silently choose it or assume account eligibility.
- Official Spotify-source docs state Soloist cannot be used with Docker bridge
  networks. Existing MA is bridged, and no network/topology change is authorized
  in intent2. Resolve engine/topology with the parent/user before claiming
  Spotify delivery. No host-network switch or LAN discovery proof was performed.

## LAN onboarding exposure checkpoint (intent5)

Added only `192.168.68.74:8095:8095` to the existing MA service's persistent
`compose.yaml`; retained loopback8095, loopback8097 and localSlimproto3483.
MA-only `docker compose -p garage-audio -f compose.yaml up -d --no-deps --pull never
music-assistant` succeeded. Exact pinned image digest and bind mounts stayed the
same. Dashboard/scanner container IDs stayed unchanged; no native DSP/device
start, queue action, public bind, router/firewall/host-network change or dashboard
human-authority exposure was performed. Playback stream remains closed.

Off-host requests to LAN `/` and `/info` returned200, MA2.10.5/schema65/
onboard_done:false; a real off-host `/ws` upgrade returned101. Desktop browser
tool returned `browser.disconnected` for this worker, so no render/phone success
is inferred. Parent can verify render in its connected browser; user confirms
the physical phone setup. LAN HTTP is unencrypted and for trusted home Wi-Fi
only, not internet forwarding.

Prior compose and complete recreate stdout/stderr/exit plus container before/
after inventory are retained at
`/home/zach/garage-audio/state/lan-ui-receipts/20261010T0247/`.
Rollback: restore that directory's `compose.before.yaml` to the project
`compose.yaml`, then run the same MA-only no-pull/no-deps compose command. Do not
restore/clear MA data or user accounts during a port-binding rollback.

## Spotify callback address correction (intent6)

The user reached MA on the phone, but a Spotify callback advertised Docker's
private address. No authorization URL/code/opaque flow ID was handed to this
worker, opened, copied, replayed or exchanged.

Supported MA2.10.5 setting is `core/webserver/values/base_url`. MA was stopped
alone, the existing settings file backed up privately, and only that supported
setting set to `http://192.168.68.74:8095`, then MA alone restarted. Its image,
container ID, mounts and other containers stayed unchanged. Provider/player,
server-identity and encryption-key values matched the prior file after restart;
the worker did not modify the authentication database. No reset or vendor-code
patch was used. Existing account sessions/user OAuth remain user-owned.

Live off-host and loopback `/info` now report the LAN base URL. LAN `/` returns200,
websocket upgrade101, anonymous `/auth/me`401. In the installed service source,
`SetupSession.callback_url` joins `mass.webserver.base_url` and its callback path.
Evaluating that installed property with the live advertised base and a deliberately
noncredential diagnostic path produced the LAN callback host. No actual user
setup flow was started by that check; the diagnostic callback endpoint returned404
because it intentionally has no flow. This is configuration/constructor proof,
not Spotify OAuth success or audible playback. Browser transport remains unavailable.

Private backup/readback/stop-start receipts:
`state/lan-ui-receipts/20261010T0257-base-url/`. The settings backup contains private
application state and must not be printed, uploaded or shipped. For rollback,
stop MA alone, restore ONLY the previous base-url field from that backup into the
latest settings file, then restart MA. Do not blindly restore the entire backup
over any newly saved account/provider data.

Phone retry: return to `http://192.168.68.74:8095`, reload the original MA settings
page and start a **fresh** Spotify login/setup attempt. Do not edit or reopen the
old callback link. Spotify Premium is already confirmed; no repeat question or
password/key pasted into chat is required. User completion of fresh OAuth is
pending, and native audio remains unavailable/unstarted.

## Soloist temporary pairing discovery diagnosis (read-only)

MA2.10.5 is still on `garage-audio_default`, address172.18.0.2. During the
user's pairing step, Soloist PID116 was alive with `--pair`; PID118 was also
alive. Only executable names, pair-flag presence and process state were read
out, never the API-key argument. A listening socket belonging to PID116 was
port46563. Inside the bridge namespace, bounded DNS-SD browsing resolved a
Spotify Connect advertisement to **172.18.0.2:46563**. No host port mapping exists
for that port; a targeted Mac→192.168.68.74:46563 connect was refused.

A six-second Mac LAN DNS-SD browse found four Spotify records, but none matched
the bridge pairing advertisement's normalized service-name hash. This is stronger
than simply searching for a friendly device name: the advertisement's instance
name is not necessarily “Music Assistant Pairing.” The matching bridge endpoint
belongs to the actual `--pair` process. Private browse receipt:
`/private/tmp/opencode/ma-soloist-mdns-20261010T030828Z/`.

The first download is therefore no longer the blocker: an actual pairing binary
is running and advertising on the Docker-private network, not the phone LAN.
This matches official MA docs' Soloist Docker-bridge incompatibility. It is
independent of the absent Garage Speakers playback transport. Phone local-network
permission/same-Wi-Fi/VPN can matter after the server advertises correctly, but
there is currently direct server-side evidence; no phone fault is asserted.

No restart, engine choice, network change, OAuth exchange or audio action was
performed. Installed source confirms selectable Soloist/librespot backends and
librespot's alternative browser playback-approval method. That browser method
can avoid temporary LAN pairing for the MA **music source**, but community-engine
playback compatibility still depends on an eligible Premium account (typically
created before December2024); eligibility is not assumed. It is not a drop-in
guarantee for Spotify-app Connect discovery.

Recommended next decision: retain the already selected official Soloist engine
and request authorization for **MA-only host networking**, with explicit bind/
advertised-address and port review before applying. Current container listeners
include wildcard8095/8097 and dynamic pairing ports; blindly changing network
mode would expose more than the current published ports. Host80/scanner and8098/
dashboard must remain untouched; local3483 and disabled extra CLI/discovery ports
must be reconciled. This diagnostic does not authorize that topology change.

Sources: [Spotify source](https://www.music-assistant.io/music-providers/spotify/),
[Spotify Connect](https://www.music-assistant.io/plugins/spotify-connect/),
[Squeezelite](https://www.music-assistant.io/player-support/squeezelite/).
