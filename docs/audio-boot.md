# Frozen NETWORK boot transaction

The operator runs `garage-audio-network.service`, not `docker compose up`. It exposes
no HTTP/MCP endpoint and has no listening/sound grant API. Install its narrow root-owned
inputs with `scripts/install-audio-network.py RELEASE MANIFEST_SHA256` during the parent's
authorized deployment phase; this installer does **not** start/enable services. Required
host tools: Docker/Compose, iproute2/nsenter, nft, busybox udhcpc, tcpdump, Python 3.
Existing native source and reviewed cancellation rollback remain unrelated and retained.

Write `/etc/garage-audio/audio-boot.json` root-owned mode 0600. Exact schema:

```json
{
  "project": "garage-audio-next",
  "compose": [{"path": "/opt/FROZEN/compose.yaml", "sha256": "ACTUAL_SHA256"}],
  "environment": {"path": "/opt/FROZEN/deployment.env", "sha256": "ACTUAL_SHA256"},
  "hook": {"path": "/usr/local/libexec/garage-audio-network/dhcp-hook", "sha256": "ACTUAL_SHA256"},
  "ma_settings": "/home/zach/garage-audio/state/ma/settings.json",
  "receiver_binary_sha256": "ACTUAL_AUTHORIZED_SOLOIST_BINARY_SHA256",
  "images": {
    "audio-device": "sha256:ACTUAL_AUDIO_IMAGE_ID",
    "audio-transport": "sha256:ACTUAL_AUDIO_IMAGE_ID",
    "music-assistant": "sha256:ACTUAL_PATCHED_MA_IMAGE_ID",
    "audio-observer": "sha256:ACTUAL_AUDIO_IMAGE_ID",
    "dashboard": "sha256:ACTUAL_APP_IMAGE_ID"
  }
}
```

All frozen input paths and parents must be root-owned and not group/world writable.
Supply the complete overlay order: base, dashboard, audio, audio-network, hardware,
audio-boot. Package owner must reconcile existing MA data mounts and remove any old custom
provider override. MA state stays in place, not cloned. Environment contains immutable
image IDs and authorized credential **file references**, never credential values.
Required observer references are `GARAGE_CONNECT_PROVIDER` and
`GARAGE_MA_TOKEN_FILE`; no extraction, provider creation or invented consent is performed.
Parent/app owner stages supported `spotify_authoritative` mode and preserved genuine setup.
The operator projects only ws.addr/ws.port to `/network/receiver` after verifying the
actual loopback listener's kernel socket owner, UID, namespace and pinned public binary.
Observer cannot read MA's private data directory or key stores. Missing listener at
startup stays OFF; listener/owner replacement after qualification revokes the generation.

The helper refuses to alter retained MA configuration if any running container owns that
data directory. Once cutover owner has quiesced the old MA, it privately saves original
settings and changes ONLY supported stream/webserver bind/publish fields to this current
DHCP lease. Provider/account/queue/setup values are untouched. An actual DHCP renew with
unchanged address continues the generation; lease/address/owner/helper/guard/dependent
process loss revokes it irrevocably. Worker acknowledgement precedes container shutdown.
Systemd failure recovery recreates all stateful dependents in a NEW generation, never
Compose restart/rejoin. Startup baseline and snapshots have no playback authority.

All roles are created and registered before start. Worker is first in revocation order.
App entry waits for the unnumbered private management veth and exact current /32 route;
`MA_URL=http://music-assistant:8095` stays fixed with the operator-owned alias. Observer
and worker fence every authority lifetime. Public silence/Stop does not depend on inference.
Dynamic LAN pairing remains CLOSED until actual Soloist listener/binary/socket ownership
classification is qualified. Do not open a range or receiver WS/control port to work around
that gate. Actual first-PCM receiver-level/gain settlement still needs its real components.
The helper queries actual public `_spotify-connect._tcp.local.` advertisement in the MA
namespace and admits ONLY a matching LAN/wildcard TCP listener owned by that same pinned
receiver, excluding its loopback WS and all MA/DSP control/stream ports. Listener/owner
replacement revokes the generation; an empty or indeterminate classification stays closed.

`Dockerfile.audio.cached` is an audited narrow derivative of cached image ID ece60bd7…,
checked against `config/audio-runtime.lock.json`. It performs no apt install/pull. The
manifest fingerprints actual binaries, installed package versions and Python distributions;
it is NOT a signed apt artifact/snapshot lock or fresh-clean-install reproducibility proof.
The packaging owner must verify the exact base local image ID before build/export/load,
freeze the complete source inventory and record the resulting derivative ID. The original
Dockerfile.audio remains a separate unqualified fresh dependency recipe. No paid calls.

Changed-boundary live proof: tests/audio_boot_live.py uses actual DHCP, Compose, MA,
Squeezelite, PipeWire and Camilla FILE, with explicitly synthetic receiver/WAV/identity and
observer process seams. It verifies all-created-before-start, private route, no autoplay,
fresh delta PCM, dependency loss, worker-first closure, stale rejection and new generation.
This is software lifecycle evidence, not Spotify setup/gain qualification or physical sound.

Quiet hardware preflight entrypoint is `python -m garage_audio.audio_zero_check`, executed
by the parent in an isolated device-worker container with the same policy/device mappings,
exclusive DeviceLease and current root generation mount. It accepts NO receiver/auth event:
only synthetic zero bytes are fed to real Camilla; it stays muted at -60 and never unmutes
or sets a higher gain. Scalar readback states sound_authority=false and no physical silence
claim. Config/stderr/exit and sanitized phase/type/errno on failure are retained privately.
This command cannot run before the exact /etc/garage-audio/docker-device.json policy and
node permissions/lock are provisioned; do not bypass that prerequisite with a manual grant.
