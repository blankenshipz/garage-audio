# MA-owned attended first speaker check (candidate; review before deployment)

This document records the diagnostic intent16 path. The intent17 normal-listening
candidate extends the same provider domain as **Garage Speakers control**; see
`normal-listening.md`. The diagnostic120s/−60 scope remains optional and separate,
not the runtime or recurring entry gate for ordinary music.

This is a **first check**, not ordinary unlimited listening. Only OUT3/4, fixed
−60 dB, maximum 120 seconds. No song starts, no gain ceiling changes, no room or
acoustic verification is recorded. MA volume remains upstream software volume.

## Phone sequence

Existing Music Assistant login → Settings → Providers → Garage speaker check →
Options. **Prepare a quiet first speaker check** stops pending transport audio
without deleting the queue and opens the existing native zero graph. After that
readback, a separate button says **I am beside the amplifier with volume low ·
Start OUT3/4 check (−60 dB, 120 seconds)**. The actual logged-in human presses it.
Then select Garage Speakers and start music in MA. **Stop speaker output now**
or the deadline closes the physical output; pause the music queue separately.
Dashboard Stop remains available. No browser codes or terminal are involved.

Provider add/config is zero-input and silent; installing it is not enrollment of
a human or authorization of sound. Agents must never invoke the production
attended action, log in as the user, mint MA users/tokens, or assert presence.
The user action is account-authenticated consent, not cryptographic proof of a
person standing in the room. Existing trusted-LAN HTTP interception risk remains.

## Authority boundaries

The project provider directory is a read-only addition to MA's documented provider
folder, not a fork or replacement of auth/frontend/core. It checks canonical MA
dispatch context and revalidates the live token and metadata inside MA. No bearer,
password, refresh token, actor header, or caller role is sent to the native host.
Guest/service/unknown roles, long-lived integration tokens, disabled/revoked/expired
sessions and impersonation are rejected. Background checks additionally require
the current CONFIG_PROVIDERS_WRITE permission; lost permission revokes the window.

The native controller adds `/run/garage-speakers/ma/control.sock`, root-owned 0600
inside a root-owned 0700 directory. It is mounted only into the MA container at
`/run/garage-speakers-ma`, never the dashboard/MCP. No public TCP or raw DSP endpoint.
Root administrators are trusted as before; **another root process/container is
not automatically the MA service**. SO_PEERCRED, pidfd, exact process PID/starttime/
boot, mount namespace, executable/hash and command-line hash are checked against a
private root-installed reference. The loaded provider files must be regular,
root-owned, non-writable by others and match all three pinned hashes.

`prepare`, `heartbeat`, `confirm` and `stop` are exact fixed schemas. No gain,
duration, profile, channel list, role or command passthrough is accepted. A one-use
HMAC challenge is bound to that session fingerprint, native generation and fixed
scope and kept internally, not shown as a code. The current MA session is verified
again before confirmation and at one-second server-side heartbeats. Neither phone
backgrounding nor polling renews the native deadline. Logout/expiry/revocation,
provider unload, connection loss, wrong generation or MA process death closes the
owned native window; the existing independent broker/DSP guardians remain intact.
Browser pairing cannot approve an MA-owned preparation. CLI fallback remains.

## Exact deployment plan (requires targeted clearance of sealed candidates)

1. Confirm current DAC closed and stop any owned preparation through existing Stop.
   Retain root-private native/config/unit and MA Compose/runtime rollback snapshots.
   Preserve all MA auth/provider/music/queue data and dashboard data mounts.
2. Install pinned native bundle and project provider archive from root-private
   snapshots. Provider directory destination:
   `/usr/local/libexec/garage-audio/ma-provider/garage_speaker_control`, root-owned
   files 0644, directory 0755, not user-writable. No MA core image mutation.
3. Add only two MA bind mounts: that directory read-only at
   `/app/venv/lib/python3.14/site-packages/music_assistant/providers/garage_speaker_control`,
   and `/run/garage-speakers/ma` read-only at `/run/garage-speakers-ma`. Keep current
   MA image `sha256:28023f8c0d96ca2496391f3218a6d70d7ef3ba84db846b3a3f3cd9ce7ef8aedc`,
   service UID 0, host networking/callback8095/data/auth unchanged. No new port.
   Recreate **MA only** after review; no upgrade/scanner/host restart.
4. Under normal sudo-n, inspect only this known container's pinned image and actual
   service process. Root-capture `process_reference(host_pid)` and provider hashes
   into `/etc/garage-audio/ma-peer.json`, root-owned 0600. Exact keys: `process`,
   `provider_path`, `assets` (__init__.py, manifest.json, client.py). Never populate
   this from HTTP caller data. Include no MA secrets.
5. Restart only the native speaker controller to load the reference and bind the
   private socket. No automatic DSP preparation, approval, enable, unmute or song.
   No capability expansion is requested; existing controller root/process metadata
   access is retained. Add the zero-input provider through supported MA setup using
   genuine user/admin UI, **not a fabricated user token**. User may add it in MA if
   there is no already-authorized non-secret admin provisioning path.
6. Verify new dashboard render and old Host/Origin/CSRF safety, exact UID/namespace/
   hash socket denials, actual MA provider availability and silent zero preparation.
   Production attended action stays strictly user-owned. Rollback removes only the
   two new bind mounts/provider and new immutable MA reference/native delta, restores
   pinned old MA Compose/native/dashboard, preserving data and legacy verifiers.

The reference is deliberately process/boot specific. MA or host restart invalidates
it; no lease or old approval is restored. A silent authorized administrator must
recapture the same pinned MA service process and restart the controller before a
fresh check. Automatic safe reference refresh is **not implemented or claimed** in
this candidate. This limitation must be reconciled before claiming full restart/
normal music acceptance; it is not a request for a terminal command from the user.

## Remaining acceptance

Real isolated MA→Unix→FILE tests are software safety evidence, not hardware/music/
physical-phone proof. Actual native installation, MA provider UI rendering, scoped
restart behavior and a genuinely human-attended low-gain first check remain live
gates. Normal playing beyond this check needs a separately explicit safe listening
policy and user acceptance; never silently extend duration or gain to imply completion.
