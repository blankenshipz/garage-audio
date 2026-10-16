# Docker-first packaging and installation gates

This is the selected hidden-Music-Assistant migration contract, not a claim that
the candidate is deployed. See [roadmap M0–M6 and R1–R10](architecture-roadmap.md).
Intent revision3 explicitly authorizes deploying the new Docker/chat version after
essential candidate/volume/identity/exclusive-owner/cutover gates. Prioritize that
journey, not optional repairs to the old stack. Upstream PR and same narrow pinned
patch authorization remains; no automatic music/gain changes or data deletion.
The earlier source checkpoint
221e7a7 remains historical; active MA/namespace/app changes require new inventory
and affected qualification, then refreeze after writers pause. No current release
or clean-install claim follows from cached staged images.
Keep the existing installation and private state until a frozen candidate passes
its matching gates. Do not run baseline Compose against production as an upgrade.

## One-time prerequisites, not everyday music controls

* Linux amd64 Docker Engine and Compose. Local macOS Docker can verify software
  FILE paths; it cannot prove the garage USB device or phone listening journey.
* A narrow operator bootstrap establishes stable UMC1820 USB identity, host device
  ownership/udev rules and exact groups. The web container gets neither DAC access,
  Docker socket nor privileged mode. Any realtime/memlock exception must be
  justified by measured requirements, not a blanket capability set.
* Preserve `/home/zach/garage-audio/state/ma` accounts/queues and dashboard private
  auth/data. Existing token reference remains server-side; do not rotate it merely
  to release, package it or pass its value to MCP clients. No new token ceremony.
* Measure workspace, Docker backing-store and receipt capacity before builds.
  Keep at least 2 GiB reserve **plus** expected archive/extraction, image/cache and
  test-output growth on each affected volume. No automatic pruning of anything.
* Select immutable matching Linux amd64 base images. Existing dashboard FILE
  dependencies are the pinned CamillaDSP 4.1.3 and Debian Bookworm library in
  `vendor/dsp/SHA256SUMS`; source archives and licenses are required release
  assets, not cleanup targets. Python requirements are version-pinned, but fetched
  from registries during build; this is not an offline installation claim.
  Hardware ALSA configuration/data dependencies must be resolved by audio packaging;
  the extracted FILE-only library alone is not a hardware installation.

After setup, everyday playback remains Spotify → Garage Speakers → Play. No MA
plugin page, session/start button, chat inference or terminal approval is required
for ordinary Spotify use. Agent/test-signal authority is a separate denied path.
That is the acceptance journey, not current candidate proof: upstream startup
volume can raise a lower phone setting. Research found no supported fix in current
stable/nightly/dev. The user now authorized preparing/testing/submitting an upstream
PR and using its identical pinned narrow patch locally. Exact commit/PR/patch image
provenance and real receiver qualification are still awaited; approval does not
prove correct volume or usable cold Play. Preserve production until affected gates
pass. Neither output gating nor connect-only guard helpers solve upstream writes.

## Existing-target deployment image route — bounded, not fresh install

Intent3 permits a **real new application image** derived from the exact retained
project image `garage-audio-dashboard@sha256:44038332e66f37ddca6a49e049a9c818e3392c06b8f073e29cedaed3e28b82f6`.
`Dockerfile.dashboard.cached` copies the current runtime modules/web/shipped default
profiles and per-file provenance into new layers. It does not re-label the old image
or use a source mount. Private accounts/queues/auth/profiles/photos are never build
inputs. This existing-target route is explicitly not proof of a clean/offline install
from an official base; `Dockerfile.dashboard` and official base provenance remain
the separate fresh-install recipe and its dependency availability gates.

Before each derivative, inspect the exact parent on the target (no pull): Linux
amd64, retained full RootFS prefix and official Python4-layer prefix, all12 vendor
files plus requirements lock exact hashes/set, every installed locked version,
and Camilla Python's immutable direct URL/archive hash. Parent-only stale runtime
files must fail preflight rather than remain silently reachable. Preserve parent
image/source/rollback bytes; changing installed dependencies requires a new reviewed
base, not a secret pip/apt fallback. Current parent has Python3.13.16, MCP1.30.0,
Agents0.23.1 and the already pinned Bookworm DSP/ALSA assets.

`scripts/dashboard-cached-context --receipt PRIVATE_DIRECTORY` consumes sanitized
live `parent-image.json`, `parent-metadata.json` (dependency hashes, installed
versions and runtime file sets) and `camilladsp-direct-url.json`. The directory must
be outside package inputs; use a new one per snapshot. It verifies the chain and
emits normalized `context.tar`, inventory/provenance and source/context/recipe
digests. There are no credentials/network/build/service operations in this helper.
Its scoped snapshot is **not** the final combined package freeze while owners write.

After fresh capacity admission, build the prepared tar, not the repository context:

```sh
docker build --network=none --pull=false --platform=linux/amd64 \
  --build-arg SOURCE_REVISION=VERIFIED_RUNTIME_DIGEST \
  --tag garage-audio-dashboard:UNIQUE_CHECKPOINT - < PRIVATE_DIRECTORY/context.tar
```

Use `check_prepare`/normal shell or equivalent full retained logs; verify exact
local parent metadata before the command, inspect no parent-layer transfer in logs,
and check the new image inherits all14 parent diff-IDs. No dependency RUN/download
exists in this recipe. Verify every inventoried source hash and actual module imports
as UID1000 in an isolated read-only/networknone/cap-dropALL/no-device container.
Metadata/path/permission failure stops; retain failed receipts and images, never
clean up automatically or relabel a failed verification green.

The first source-copy build succeeded but its directory chmod0644 prevented non-root
traversal; its failure/image is retained. Corrected recipe preserves normal directory
permissions. The affected rebuilt checkpoint passed all81 runtime file hashes and
webserver/MCP/Spotify-setup imports. Exact immutable image ID/source/context/gate
receipts are external, not progress narration baked into package inputs. Four new
layers added713,668 logical bytes; total observed target growth5,021,696 bytes
(includes concurrent writers), versus128MiB budget. Free target after gate was
2,742,190,080 bytes:2GiB reserve plus256MiB named receiver headroom still retained.
That headroom is not actual receiver-download/cache qualification or admission for
new apt installs. No parent/dependency pull, production start or cutover occurred.

This resolves a concrete application-image build route without Desktop resize,
receipt deletion or unrelated-project restart. Final audio/MA strict patch/image
and runtime provenance, private boot/namespace/device prerequisites, matching
app/auth/MCP/UI proof and preserved silent rollback remain essential. Existing owners
must pause before the combined final inventory/freeze; rebuild the small affected
application layer if their runtime sources changed, never relabel this snapshot.
Require their exact incremental image/receiver/cache growth budgets; no fresh apt
or receiver allowance is inferred from the small dashboard layer.

## Combined topology and ownership

Paused-owner package inputs now include `Dockerfile.audio.cached`, exact runtime
fingerprint `config/audio-runtime.lock.json` (ac0c716e), strict MA4796/5b523a patch,
and 33-file boot inventory623d54c3. The built audio, patched MA and final app image
IDs are pinned in `config/docker-release-inputs.json`. Runtime checks passed:
7 binary hashes/242 dpkg/58 Python entries, six exact patched MA files/new enum,
and the superseding enrollment app's88 source hashes/imports at UID1000. These are executable
image checkpoints, not receiver/gain/device/cutover acceptance. PR publication
awaits authorized GitHub auth and is separate from the essential cutover gates.

**Historical first combined-build capacity gate failed (receipt retained).** The tiny
new layer total (about2.7MB) did not bound physical build growth: cold MA base
extraction created a new approximately2.5GB BuildKit snapshot despite the base
already being an inspectable engine image. The retained build log explicitly
shows extraction, not a dependency install. Peak observed free space fell to
205,791,232 bytes; later read-only metadata observed538,718,208 bytes. Both are
below2GiB reserve at that time. Original matching MA/audio FILE/Stop/no-autoplay
proof was subsequently obtained after the specifically authorized capacity repair;
its original1f3b9081 identity remains, not new enrollment-app acceptance.
Complete failed receipts/images are retained externally; never relabel the
runtime-pass portions as a whole successful release gate or prune automatically.

Root specifically authorized and executed the existing-LV online4GiB extension:
`/dev/ubuntu-vg/ubuntu-lv` and mounted ext4 now111,669,149,696 bytes (104GiB),
exactly1024x4MiB extents consumed, no observed pre/post errors; current VG free
141,113,163,776 bytes. No repartition, deletion, Docker Desktop change or service
restart occurred. This records that bounded authorization, not permission for
further growth. The final app-only thin derivative's capacity/runtime gate passed
with5,283,840 bytes observed growth,803,548 new-layer bytes and4,395,323,392 bytes
available afterbuild, preserving2GiB+256MiBreceiver+512MiBtest allowances.
Remeasure before bounded tests; no new cold-cache build is admitted. No protected
image/account/queue/receipt must be removed. Cache retirement is not automatically authorized and
shared-cache metadata alone does not prove a minimum physical reclaim amount.

Local source/archive inventory may continue on the Mac with its own measured
reserve. After the last source/doc/manifest edit, freeze once, record the digest
and later live gates externally, and do not mutate inputs to narrate results.
Recheck target capacity before sending/extracting that archive or doing FILE,
receiver or hardware gates. [App-only enrollment deployment](ma-enrollment-deploy.md)
is a separate partial phase; exact new app pin/full source map are reconciled,
but real admitted peer/render/restart/phone enrollment remains pending. Root-owned credential/reference/device-policy
bindings remain external prerequisites, never fake values baked into this package.

The intended candidate uses all three inputs with one explicit Compose project:

```sh
docker compose -p garage-audio -f compose.yaml \
  -f compose.dashboard.yaml -f compose.audio.yaml config --quiet
```

`compose.audio.yaml`, `compose.audio-hardware.yaml` and `Dockerfile.audio` are
owned by the audio lane and must exist before combined inventory/freeze. The
hardware overlay is a required package input, **not** permission to run it: exact
device policy/host bootstrap and exclusive quiet M5 cutover are prerequisites.
MA retains host networking for LAN
discovery in the **baseline** only. New audio qualification uses a private bridge
for M2; production Soloist socket isolation/LAN discovery (private namespace or
pre-publication host guard) is unresolved and must be settled before M5. No
macvlan address is invented or reserved by this guide. A real DHCP OFFER/ACK was
obtained and released in an owned isolated namespace: allocation is feasible, not
a license to reuse its released address. The audio owner is implementing renewable
carrier lifecycle/address-loss fencing and private management routing. Never
hardcode the historical lease or replace this boundary with a connect-only/racy
guard. The parent-fixed private management contract is `music-assistant:8095`
on the project-owned backend network; the audio owner supplies its actual carrier/
routing/assets and proves that alias reaches only the candidate. No DHCP address
is embedded in app configuration. This contract is not live routing proof and
grants no web Docker/root/device access. Exact upstream setup enum remains awaited;
packaging neither invents one nor edits the app/audio owners' implementation.
Dashboard has its own
project-owned `music` bridge plus the candidate's `audio-backend` network. MA uses
service DNS there: the old LAN `extra_hosts` override is removed so an isolated
candidate cannot silently talk to the legacy MA instead. A fresh checkout does
not need an external retained network.
Base Compose uses explicit retained `/home/zach/garage-audio/state/ma` and
`/home/zach/garage-audio/state/music` defaults (operator path variables available),
never release-relative state. Actual production bind metadata confirmed these
paths. The new overlays must contain neither the historical custom control-provider
site-packages mount nor `/run/garage-speakers-ma`; old production mounts/data remain
untouched until the separate quiet exclusive cutover/rollback phase.
Preserve the existing authorized localhost and `192.168.68.74:8098` dashboard
bindings, exact authorities and Host/Origin/session/CSRF checks. Host networking
does not authorize exposing MA/raw DSP control publicly. Review actual MA listener
configuration before cutover; container config alone does not prove listener scope.

Do not independently activate dashboard Compose under a different project after
the combined stack is installed: that creates different ownership/network labels.
The agreed public IPC is UID1000 plus GID1003, only the read-only
`audio-public:/run/garage-audio/public:ro` mount and server-owned
`GARAGE_AUDIO_CONTROL_SOCKET=/run/garage-audio/public/control.sock`. Dashboard
does not see event/transport sockets, DAC or the historical `/run/garage-speakers`
bridge. Docker mode is explicit; native fallback requires a separately chosen
`GARAGE_DEPLOYMENT_MODE=legacy-native`, never failed Docker readiness. No runtime
audio success is inferred from Compose validation. The native builder's ignored `tools/`
assets are not dependencies of the new Docker install path or proof of bootstrap.

## Inventory, freeze, gates

Credential-free provisional inventory (writers may still be active):

```sh
python3 scripts/dashboard-stage --inventory --docker-stack
```

The helper writes hashes and observed local filesystem capacity to a unique private
receipt outside shipped inputs. Missing required inputs or symlinks fail closed.
This observation is not a frozen revision, Docker free-space measurement or gate.
The package includes project modules, web/favicon, profiles, docs/contracts, vendor
pins/sources/licenses, scripts/config/tests; state, tools, Git and caches are excluded.
`.dockerignore` additionally limits normal local Docker build context to project
inputs and excludes `.env` files. Never place secrets inside project input folders.

Top-level `Dockerfile.*` and `compose*.yaml` are discovered automatically, including
the audio owner's pending `Dockerfile.ma` and carrier recipes. Patch/provenance
under `vendor/`, lifecycle helpers under `scripts/`, modules/config/docs/tests are
inventoried; do not silently put required assets in an uninventoried directory.
`config/docker-release-inputs.json` names required MA/rollback inputs and owner
manifest references. Null references are **work awaiting owners**, not fake pins.
`--inventory --docker-stack` reports this unresolved state. Add `--release-inputs`
to inventory/freeze only once the patch, network and runtime-lock manifests and
immutable official base digests are reconciled; it fails closed before a gate.
It is an input check, never permission to deploy or independent test proof.

Only after the coordinator pauses all package writers and reconciles interfaces:

```sh
python3 scripts/dashboard-stage --freeze --docker-stack --writers-frozen
```

This retains `candidate.tar`, `inventory.json`, `candidate.sha256` and scope in a
private receipt. Check matching backend/platform/capacity before substantial gates;
retain full stdout/stderr/exit status against that exact digest. Input changes
invalidate affected evidence and require a new candidate, not relabeling old proof.
The historical helper's `--deploy` is **dashboard-only** and rejects combined stack
candidates; it is not an audio cutover tool. M5 requires a separately verified
exclusive DAC owner transition, quiet restart/no-autoplay and retained rollback.
The current `--freeze` is an **archival source freeze only**. Runtime apt package
versions are not release-locked, base/platform clean installation remains unproven,
and hardware bootstrap is implemented but unactivated. Source-checkpoint scope,
open gates and post-freeze results belong in the external receipt; do not modify
package docs merely to paste that archive digest back into them.

## Clean-install inputs and exact proposed runtime lock

* MA: official `ghcr.io/music-assistant/server:2.10.5` by the existing immutable
  digest in the input manifest; local image derived with the identical reviewed
  upstream patch, base/source hashes, patch SHA256, PR URL/commit and update/removal
  policy. No fork/PR merge status is fabricated; the upstream owner supplies it.
* Dashboard: official Linux amd64
  `docker.io/library/python:3.13.16-slim-bookworm@sha256:f040863673aea2570c3ff6a5c3fb4c673a016cbc5375005ad145915922b6b78a`
  resolved by public registry metadata only. `Dockerfile.dashboard` defaults to
  this immutable child manifest; `BASE_IMAGE` remains explicit override control,
  requiring equivalent reviewed platform/base provenance. Its early dpkg amd64
  guard protects the architecture-specific DSP/ALSA inputs. Use `--platform
  linux/amd64` for a future admitted build; no image was pulled or built here.
  [Sanitized provenance](../config/docker-python-base-provenance.json) records
  index/config/layer hashes and sizes, official source and Bookworm base annotation.
  Python3.13.16 matches cached runtime metadata, not a claim of identical images.
* Audio: derive from that built dashboard artifact, then install the reviewed
  Bookworm runtime lock. Existing private cached dashboard/audio IDs are suitable
  exact-ID staged-test inputs, **not** a fresh-install official base distribution.
* Preserve vendor source archives, licenses and hashes, all required setup assets,
  and [rollback contract](docker-rollback.md). The historical ignored `tools/`
  native builder is not called by this Docker path; it remains historical rollback
  source, not a mandatory fresh Docker installer dependency.

Proposed direct versions observed in cached audio imageece60bd7 (amd64 Bookworm):
`pipewire=0.3.65-3+deb12u1`, `pipewire-alsa=0.3.65-3+deb12u1`,
`wireplumber=0.4.13-1`, `squeezelite=1.9.9-1414+git20221121.dbe69eb-1`,
`dbus=1.14.10-1~deb12u1`, `libasound2=1.2.8-1+b1`,
`libasound2-data=1.2.8-1` (all architecture). Send this proposal through the parent
to the audio owner; packaging does not edit `Dockerfile.audio`. Before calling it
a clean-install lock, that owner must inventory direct **and transitive** packages,
verify archive/repository hashes and immutable snapshot provenance, and reconcile
namespace helper dependencies and the approved official base. Version observations
alone do not prove reproducibility or that packages remain available in a registry.

Official Python base layers total46,484,719 compressed bytes (44.3MiB); expanded
sizes were not downloaded/measured. Planning estimate: compressed store plus5x
expansion278,908,314 bytes; allow1GiB for new Python/DSP/audio installs/build cache
and128MiB for receipts/receiver growth, **plus2GiB reserve**: estimated minimum
3,634,351,514 bytes (3.38GiB) free. This allowance is not a bound or build admission;
apt-lock/receiver estimates must replace it before real gates, with fresh capacity.
The prior measured target2,983,534,592 bytes and local Docker2,268,061,696 bytes do
not cover that estimate. Do not prune/pull to resolve it automatically. Prefer the
cached target source-bound isolated gates, budget their actual incremental growth
separately, retain failed receipts, then seek capacity for a fresh clean-install gate.

Read-only cache comparison found all **four** official base config diff-IDs exactly
equal the first four RootFS diff-IDs of target dashboard44038332 (14 layers total).
That verifies identical uncompressed base-layer contents, not cached official
manifest/blob mapping or replay of pip/apt instructions. Conditional reuse can
remove the estimated232,423,595 bytes base unpack allowance; retaining the46,484,719
compressed allowance leaves3,401,927,919 bytes estimated required free. Latest
target free2,966,835,200 bytes still falls435,092,719 bytes short. Exact new apt/
Python/MA patch/receiver/download headroom estimates are required before admitting
even a cache-aware build. This is not a new source freeze or full-build readiness.

Dependency-probe preflight inspected actual target BuildKit0.33.1/docker-driver
records: old DSP extraction, vendor/requirements COPY and pip install records are
retained. Earlier dashboard buildlcajgn9n resolved official basef0408636 in local
mode and reported those dependency steps CACHED. All12 vendor files plus the lock
match current source by exact file set and SHA256. This establishes useful cache
provenance, not a new cache hit. The added architecture guard changes the original
DSP RUN; it may move after dependencies only for an admitted exact-order target,
with the immutable amd64/platform preflight retained.

**No offline dependency probe admitted:** the official base is absent from daemon
image metadata both by digest reference and public config ID4143a73d. Retained
BuildKit source snapshots/history do not establish a current fail-closed local
manifest/config resolution path. `--network=none` isolates RUN, not image-source
resolution; `--pull=false` alone is not a ban on a missing image's payload transfer.
Do not risk an unbounded base extraction or rerun an unchanged miss. A probe requires
current no-payload local-resolution evidence and a worst-growth budget≤128MiB,
fresh2GiB reserve plus named extra headroom, unchanged dependency inputs and private
`check_prepare` logs. A dependency cache miss must stop offline, never enable network
or download pip/apt packages. No full/private-image rebase is a clean-install substitute.
Latest preflight target free2,963,357,696 bytes still does not admit the conservative
fresh build; receiver/download growth and audio apt installs are separate estimates.
Existing source-bound staged tests remain a distinct bounded route, not frozen/full-
image proof. Root capacity options above remain unapproved and must be remeasured.

### Docker Desktop capacity option — root decision, no changes applied

Official supported control is **Settings → Resources → Advanced → Disk usage
limit** ([Docker settings](https://docs.docker.com/desktop/settings-and-maintenance/settings/)).
Read-only settings show61,035MiB logical disk limit; latest VM free2,266,144,768
bytes falls1,368,206,746 bytes below the conservative fresh estimate. A2,048MiB
increment would set63,083MiB (logical disk66,147,319,808 bytes). Do not edit JSON,
truncate/move disk images, switch image stores, restart Docker or apply this proposal
without the root's explicit approval and fresh capacity/impact checks.

Critically, Mac available6,384,025,600 bytes and current Docker.raw allocated
58,294,472,704 of63,999,836,160 logical bytes mean **full allocation** of the
expanded disk would leave **negative1,468,821,504 bytes**, not the required2GiB
host reserve. Another3,616,305,152 bytes of safe host capacity would be needed
before treating that full-allocation proposal as safe. Counting only the new2GiB
increment would falsely show4,236,541,952 bytes left while ignoring existing sparse
growth. `/Volumes` contains only the same Macintosh HD, no already-mounted alternate
project filesystem was identified; no relocation/new storage is authorized here.

Applying a Desktop resource change may restart its Linux VM: five observed running
local containers belong to unrelated yrdsale/demodirector services and databases.
Coordinate their owners and application-safe interruption/recovery before any
approved change; no automatic stop/restart, prune or deletion. The narrow cached
target source-bound gate remains the best next option after remeasuring target
free space and bounding its incremental output to the proposed128MiB budget;
receiver growth still needs its own evidence. Local workspace/receipt capacity is
not interchangeable with Docker backing-store capacity.

### Dashboard shutdown packaging

`compose.dashboard.yaml` retains `init: true` and now uses
`stop_grace_period: 30s`, covering the15s MA public-operation deadline and5s handler
read/drain timeout with margin. Targeted Compose normalization verifies30s/init,
the fixed private `music-assistant:8095` alias and unchanged no-DAC/no-Docker-socket
mount restrictions. This configuration check is not another runtime shutdown test.
Actual app lifecycle sourcebdae7860/webserverf5959ba6 passed pending-request drain,
FILE Stop, exit0 and retained cancelled-chat/unchecked-terms restart state on the
cached source-bound fixture; official stock MA separately exited0 with init.
Original failed fixture exits/logs remain retained; no clean patched-MA restart,
release/image/device gate or everyday Spotify activation follows from this proof.

## Next gate plan, not an executed release

1. Reconcile exact owner patch/carrier/apt manifests and rollback prerequisites;
   cheap required-file/platform/config/hash checks only while writers are active.
2. Use target cached official MA and reviewed dashboard/audio IDs for the smallest
   isolated source-bound/pinned-patch FILE/namespace journey, exact source/image
   identities and private external full stdout/stderr/exit receipts. Never use
   production state, DAC, listener replacement or a released lease. Proposed first
   gate growth budget128MiB, plus2GiB reserve; actual receiver-download/cache growth
   needs a separate budget before admission. No blanket capacity assumption.
3. After writers pause, refreeze explicit inventory with `--release-inputs` and
   declare the candidate digest. Run only affected real component gates against
   that candidate via `check_prepare`/normal shell or equivalent retained runner.
4. Fresh clean-install/build and M5 require a separate measured image/cache/receipt
   growth budget and matching immutable inputs; cached success is not that gate.
   No prune, paid build fallback, broad full suite or automatic production switch.

## Existing real MCP transport

`scripts/dashboard-mcp` runs `ssh -T -o BatchMode=yes`, then `docker exec -i
garage-audio-dashboard python -m garage_audio.mcp_server`. Stdout is MCP only;
there is no public agent port, TTY or client token. `GARAGE_APP_TOKEN_FILE` is the
container's private reference. The adapter reads it locally, authenticates to exact
loopback `GARAGE_APP_URL`, retains a session/CSRF value and uses shared HTTP typed
operations—not independent app state. Non-loopback origins are rejected; missing
credentials/service produce structured errors without disclosing credential bytes.
See [agent access](agents.md) for the actual live initialize/list/status → quiet
mutation → MCP and HTTP/UI readback acceptance. Source inspection is not that proof.

Inference remains disabled even with ambient provider credentials. Setup expiry,
test shutdown and chat cancellation must not revoke enrolled daily Spotify use;
restart must not restore sound, stale grants or prior playback automatically.

## Bounded no-build development observation

A read-only source/dependency mount into an existing cached Linux image can verify
new app/MCP/HTTP operations against isolated persistent state without building or
replacing production. It is not a frozen image, deployed SSH or hardware gate.
The source-bound local checkpoint in `/private/tmp/opencode/garage-m3-sourcebound-T0qgih`
initialized/listed real Docker stdio MCP, saved a display name, matched fresh MCP
and container-internal HTTP readback across Stop/restart, and verified served UI
HTML against source hashes. Credentials stayed in a private server-side volume.
Missing public audio socket produced truthful `audio_unavailable`; no worker,
browser-rendering, device, sound or final packaging success follows from that check.

The later isolated **garage SSH** target journey paired exact staged app/web source
with the frozen r13 FILE worker: real MCP setup mutation matched HTTP and actual
desktop/390px Chromium UI, including successful FILE Stop, focus/navigation and
reload-name readback. See canonical roadmap R9 for original receipt identities.
After UI proof, only its owned app/FILE pair and loopback SSH forward were stopped;
all test state/source/receipts remain, production MA/dashboard remain on their
original images. Physical Safari/phone/DAC/sound and frozen immutable M5 release
are separate outstanding acceptance, not inferred from these staged tests.

Latest app-owned Soloist protocol was exercised against actual isolated garage
MA2.10.5 through real SSH MCP and HTTP: begin/backend choice/unchecked terms/readback/
abort only. No consent, key, download, auth or provider creation. New desktop/390px
missing-credential UI was rendered at web3d38be09; later authenticated unchecked-
terms active forms also passed desktop/390px refresh/focus/FILE Stop/Cancel in the
owned sandbox, with no consent/key/provider creation. App lifecycle now has the
bounded exit0/drain/restart evidence above; prior timeout-stop receipts remain
failures at their original identities, not relabeled success. Updated
audio source passed actual MA/transport/DSP FILE, with
synthetic receiver/WAV seam. Hardware identity/bootstrap remains unactivated.
Canonical roadmap checkpoints retain original source/image identities and receipts;
no prior FILE, UI or protocol proof automatically becomes final-image/M5 evidence.
