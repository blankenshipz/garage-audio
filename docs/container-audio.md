# Private container audio checkpoint

The qualified checkpoint is **FILE-only, not a deployed DAC stack**. An unactivated
identity-bound hardware implementation now exists, separately from that proof. Existing native/MA production
services remain untouched. Do not apply the combined Compose candidate to production yet.

## Interface for app and packaging owners

`Dockerfile.audio` consumes a reviewed Bookworm dashboard/DSP base, containing pinned
CamillaDSP4.1.3 and its verified Bookworm ALSA library. A bare Docker image config ID is
not a valid `FROM` reference; an existing local tag must be byte-checked against its
expected image ID before a test build. Release builds must inventory/pin all base/package
inputs; apt package installation in this checkpoint is not a reproducible release lock.

`compose.audio.yaml` combines with `compose.yaml` and `compose.dashboard.yaml` under
`-p garage-audio`. It overrides MA host networking to a private audio bridge; this does
**not** qualify production LAN discovery. No ports or devices are published by the audio
overlay. The app currently reaches deployed MA through its separately configured address;
matching candidate MA/app routing must be reconciled before cutover.

The only public audio IPC is `/run/garage-audio/public/control.sock`:

* newline JSON, at most4096bytes, no extra keys;
* `{"operation":"audio.status"}`, `audio.pause`, or `audio.stop`;
* reply `{"ok":true,"result":{...}}`, or bounded `{"ok":false,"error":"..."}`;
* kernel peer UID1000 required; socket owner1002/group1003/mode0660, parent0710;
* pause/stop close owned PCM/DSP output. The app must separately use existing supported
  MA silence operations if it also wants to pause/stop upstream playback;
* no Play, seek, gain, arbitrary subprocess/path, raw DSP, or grants accepted;
* result truthfully identifies `device_mode:file`, `device_open:false`,
  `physical_verified:false`, generation/state/reason, deadline and child status.

Dashboard mounts only named volume `audio-public` read-only at that path and joins
group1003. Use environment `GARAGE_AUDIO_CONTROL_SOCKET` as the private server-owned
reference. This is not a browser token, a setup approval, or a second HTTP implementation.
`setup.readiness` must label hardware/production topology unqualified, not infer readiness
from a running FILE child.

The internal event socket `/run/garage-audio/events/event.sock` admits only UID1001;
owner1002/group1001/mode0660, directory0710. It is never mounted into dashboard/MCP.
UID1001 transport/observer and UID1002 device worker are the trusted audio boundary.
Only those containers mount the private PipeWire volume; its socket is0660/group1003.
Container-specific WirePlumber0.4 policy disables ALSA/video/Bluetooth/logind discovery
and portal integration; it admits cross-PID-namespace clients at this private socket.
It must not be installed into the native or desktop PipeWire graph.

## Execution and evidence boundary

MA and Squeezelite registration persist while device worker is trulyOFF. Fresh current-epoch
trusted deltas prepare a one-use 1.5-second worker ticket; only matching corroborated
ticket redemption can start owned pw-record + actual Camilla FILE children; snapshots and
heartbeats alone cannot. Pause kills both children and retains only a bounded paused lease;
Stop, loss, unknown/auth/device failure, deadline, child exit and watchdog close output.
Guardian never takes the command lock. Child adoption is fenced against concurrent Stop.
Linux parent-death signals close children on worker loss. Grants are memory-only; restart
startsOFF with new generation. Graph is immutable48k stereo, fixed-6dB fullrange on physical
OUT3/4, other ten outputs zero. User attenuation remains upstream Squeezelite, not a
percent-to-dB guess or an added volume stage.

`tests/compose.audio-file.yaml` and `tests/run_audio_container_file.py` launch actual pinned
MA2.10.5, supported MA WebSocket commands, real Squeezelite/PipeWire and CamillaFILE.
Receiver events and WAV program are explicitly synthetic. The fixture's private native
user creation is setup-only, never production authorization. No custom MA plugin loads.
Logs/evidence/stopped containers and volumes are retained; fixture shutdown does not
touch enrolled production MA/player/device services. Do not run the fixture with production
state mounts. Test runner shell currently targets the verified installed Bookworm base;
it is not a clean-install installer.

## Remaining qualification

`audio_observer.py` implements documented Soloist WebSocket observation plus authenticated
public MA player/queue corroboration; default invocation refuses to enable. It is **not
yet exercised with real Soloist**. Fresh Play now creates a bounded cancellable ticket
while independent receiver observation continues; MA's deferred source selection may
qualify within 1.5seconds. Pause/Stop/auth loss/epoch replacement cancel it; snapshots
cannot create or extend it. Stop preserves a valid connection baseline, so a subsequent
fresh Play can prepare another ticket. Public Stop also causes supported observer Pause,
never Play/seek/volume. These races are synthetic-test qualified, not real Connect proof.

Pinned MA2.10.5 source pushes the player's restored volume on activation and source
selection and ignores incoming Spotify volume for3seconds. Squeezelite defaults to20
when no previous volume is restored. No supported suppress-push switch was found;
a lower user Spotify level can therefore be raised. Do not compensate by increasing
volume or call this normalization. This remains a production blocker. Soloist attenuation/clock
must be qualified separately; synthetic WAV tests do not establish official Connect
synchronization. No provider inference, Spotify authentication/music or phone listening
has been performed by this checkpoint.

Public upstream inspection on2026-10-12 found no supported fix in latest stable
[2.10.6](https://github.com/music-assistant/server/releases/tag/2.10.6), nightly
2.11.0.dev2026101103, or dev commitb31ac5e55f55e883485c1e731800ff0b05314a16.
The deployed2.10.5 provider and Soloist backend bytes match2.10.6. The exposed
`volume_mode` choices are `player_only` and `sync_spotify`, not phone-authoritative
startup: player_only pins Soloist to100 and does not forward its volume changes;
sync_spotify compensates Soloist attenuation back to unity before forwarding volume
to the MA player, still subject to the provider's3second grace/startup push. See
[provider policy](https://github.com/music-assistant/server/blob/2.10.6/music_assistant/providers/spotify_connect/provider.py#L1173-L1249)
and [Soloist modes](https://github.com/music-assistant/server/blob/2.10.6/music_assistant/providers/spotify_connect/soloist/backend.py#L878-L928).
Upstream PR5303/6087 intentionally make player volume authoritative; the latter fixes
group volume lookup, not phone authority. An upgrade alone does not remove this blocker.

A worker mute gate can prevent sound while prerequisites are unknown, but cannot stop
MA writing a higher Spotify slider value. Current observer/worker has only latest volume,
no uncontaminated session-bound user-level receipt; Soloist events do not identify command
origin. Waiting3seconds or comparing two already-crossed readbacks cannot recover the
prior phone level. A proposed public upstream phone-authoritative policy would need to
suppress automatic MA→Soloist pushes/echoes, cache fresh receiver volume across source
claim, apply it to the player before first stream, and bypass player-wins grace only for
that policy. Startup/reconnect ordering and gain/clock behavior still require real
qualification. Until upstream ships it, shipping such changes requires an explicitly
chosen maintained fork, not a custom plugin/private hook or silent vendor mutation.

Production still needs either a legitimate reservedLANIP/private discovery namespace or
qualified pre-publication host socket guard, exclusive identity-bound UMC permission and
reconnect bootstrap, real watchdog/device proof, frozen matching package/backend/capacity,
silent reversible cutover and preserved rollback. No raw unauthenticated Soloist or DSP
socket may be exposed to app, browser, unrelated containers, LAN or ambient same-host clients.

## Unactivated hardware implementation (not release acceptance)

`audio_device.py`, `audio_child.py`, `config/audio-device-policy.json`,
`compose.audio-hardware.yaml` and `scripts/bootstrap-container-audio.py` are required
hardware package inputs. Bootstrap defaults to inspection only; root `--apply` requires
a frozen inventory digest/private receipt, exclusive fuser scan and a shared Linux flock.
It retains previous host file bytes/node metadata for quiet rollback. Do not invoke apply
before the root release gate; rollback still needs coordinated old-service restoration.

Exact USB1397:0514/serialD72E24B7/cardUMC1820/PCM0 requires12-channel48000Hz S24_3LE.
Only OUT3/4 are mapped at fixed-6dB. No default DAC/fallback exists. Bind only exact
playback/control nodes; read-only `/host-snd` and `/host-asound` provide current metadata.
UID1002/GID1003 mode0600 is required; old native UID999 is deliberately rejected.
Nearest USB ancestor (not root hub) binds path/inode/bus/devnum and both node identities;
changes revoke the generation. A child-inherited lock outlives parent closure until DSP exit.
Muted/-90 preparation and fixed unity master with400ms ramp/0dB ceiling are implemented
but **no hardware launch/unmute/clock behavior has been exercised**. Monitor checks identity,
clipping/ceiling; an independent guardian bounds monitor silence. Production permission
changes, USB replug, DAC open, rollback and clean-install bootstrap remain unrun.

## Owned DHCP namespace carrier (unactivated network checkpoint)

Follow-up executed actual stock MA2.10.5→Squeezelite→PipeWire→Camilla synthetic-WAV FILE
in an owned DHCP carrier. Fixed `MA_URL=http://music-assistant:8095` alias/private on-link
route reached actual MA. At100 fixture volume, scalar peaks0.152950/0.076475 matched-6dB
OUT3/4 and allten spares were zero. Real helper loss revoked root-owned authority; worker
reported OFF/no owned children before worker-first container stops; PCM stopped growing.
New generation baseline/heartbeat stayed OFF until fresh synthetic receiver prepare/delta.
Injected container-stop failure still closed PCM through worker watchdog, while receipt
retained failed stop statuses and `physical_silence_verified=false`. Not real Spotify proof.

Authority is a root-written read-only generation heartbeat/deadline plus irreversible revoke
marker, mounted only into the worker. Hardware mode rejects missing network admission.
`compose.audio-network.yaml` declares exact carrier joins, fixed alias and generation mounts;
it disables automatic restarts. It is unactivated: operator must register exact created IDs
worker-first BEFORE start, validate labels/process/inodes, install private routes, and recreate
all dependents on replacement. Current factory exercises that journey with isolated fixtures;
full persisted production Compose transaction/boot wiring remains unfinished. Do not `up`
this overlay directly or mistake Compose syntax checks for the host transaction proof.

`garage_audio.audio_network.Carrier` is host-operator bootstrap code, not an HTTP/agent
operation. `scripts/bootstrap-audio-network.py` is its dedicated installed-udhcpc hook;
it records lease events but never replaces an active namespace address. Exact container
IDs/generation labels, PID birth and namespace inode bind each lifetime; Linux can reuse
a retired inode, so inode alone is not a generation token. A root-private singleton lock
outlives owner death until the independent guard retires the old lifetime.

Entry: create a network-none carrier from the pinned cached image; install atomic
namespace-only nft firewall before DHCP/dependent startup. Attach a dedicated macvlan
`ga-lan` on enp2s0 using a distinct MAC collision-checked across host and Docker namespaces.
Installed udhcpc validates the offered address with ARP. No released address is hardcoded.
Same-address renewal preserves running dependents. This does not require a router reservation.

MA and observer must share this exact carrier namespace; Soloist WS binds127.0.0.1.
The admitted outside client reaches MA TCP8095 over an **unnumbered** management veth,
so no private bridge address can be selected as a receiver advertisement. The app needs a
host-installed on-link route `<leased address>/32 dev eth0` in its private namespace, and
server-owned endpoint `http://<leased address>:8095` plus supported `/ws` API and existing
private MA credential reference. Namespace firewall admits8095 only from the exact owned
management client, never LAN; loopback aliases and mapped aliases are rejected. IPv6
ingress is disabled/denied. No Docker socket or general NET_ADMIN is granted to apps.

Stop/exit: independent host guard checks owner/helper lifetime, lease/deadline/address,
carrier process/namespace identity. It stops exact registered dependents before the
carrier; production worker must be first in that list. A controller watchdog catches
guard death/stalled heartbeat. Owned DHCP/capture helpers retire too; receipts and stopped
containers/networks remain. Only owned temporary link effects are removed, never pruning.
Address change/NAK/deconfig/expiry/helper or owner death/restart revokes the old lifetime
before replacement. Recreate ALL affected own dependents and verify their actual namespace
join; Compose depends_on/automatic restart is not a rejoin/authority guarantee. Fresh Play
is still required after observer/worker epoch revocation; snapshots cannot resume audio.

`tests/audio_network_live.py` exercises actual Linux/Docker/DHCP/nft/management routing,
wildcard listener fixtures, loss/restart and owner-SIGKILL/fresh-rejoin roles.
`tests/audio_network.py` synchronizes real Mac LAN/mapped-IP probes with a live nft drop
counter. Synthetic wildcard listeners and injected expiry are not actual MA/Soloist or
clock/sound proof. Complete external failure/pass receipts retain original source identities.

Current factory is an isolated fixture bootstrap, not a deployed Compose stack. Management
network/client must have exact same-generation owned labels. Dynamic Connect pairing
ports are **closed**: first identify actual receiver listener/process/binary/socket, not
merely mDNS announcements, then admit only required LAN pairing traffic. Production
Compose lifecycle/credentials/backend mounts, real Soloist binds/discovery/setup, audio
lease→epoch revocation and verified normal transport recovery remain unfinished. The
cached carrier image is not the patched MA image. Wait for the upstream owner's exact
patch/config/image provenance; do not duplicate or silently mutate that patch. Hardware
cutover/replug/rollback and frozen release gates remain separate and unrun.
