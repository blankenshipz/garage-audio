# Room and listening-profile domain API (v1)

Integration: initialize `application.room_profiles = Store(application.root)` from
`garage_audio.room_profiles`. Merge `SCHEMAS` (required/optional lists) and
`OPERATION_SCHEMAS` into application capabilities/validation. Under the existing
Application thread AND process lock, dispatch
`application.room_profiles.execute(operation, args, application)`. Results are raw
JSON values, wrapped by Application's existing envelope. Catch `DomainFailure`
alongside Requirement/JobFailure (same `code,message,requirements` attributes).
Strict schema failures raise ValueError. Store never calls policy, providers or
sound APIs. Pillow is required only when uploading a photo. The root and each
ancestor must be real directories (no symlinks); existing sessions/domain
directories and records must be current-user-owned and private (0700/0600).
Create root via Application first. This is intentional fail-closed path isolation.

All IDs are server-generated 32 lowercase hex characters. Every room/profile has
`id,name,version,created_at,updated_at,archived`. Every mutation of an existing
record requires `id,expected_version`; stale edits return `version_conflict`.
Archived records are retained and immutable; lists include them. No eviction.

## Exact operations (unknown fields rejected)

| Operation | Required | Optional | Result |
|---|---|---|---|
| rooms.list | — | — | room array |
| rooms.create | name | notes | room |
| rooms.get | id | — | room |
| rooms.update | id,expected_version | name,notes | room |
| rooms.archive | id,expected_version | — | room |
| rooms.annotate | id,expected_version,annotations | — | room |
| rooms.confirm | id,expected_version | — | room |
| rooms.photo_upload | id,expected_version,data_base64 | — | room (new photo descriptor) |
| rooms.photo_get | id,photo_id | — | `{id,mime_type,data_base64,width,height,sha256}` |
| listening_profiles.list | — | room_id | profile array |
| listening_profiles.create | room_id,name,kind,fit_positions,verify_positions | target,notes | profile |
| listening_profiles.get | id | — | profile |
| listening_profiles.update | id,expected_version | name,fit_positions,verify_positions,target,notes | profile |
| listening_profiles.clone | id,expected_version,name | — | new independent profile |
| listening_profiles.archive | id,expected_version | — | profile |
| listening_profiles.bind | id,expected_version,bindings | — | profile |
| listening_profiles.guide | id | — | deterministic next import/confirmation action |

`kind`: `whole-room|seat|area|custom` (immutable). Name 1–120 chars, notes ≤2000.
Target `{low_hz,high_hz,tilt_db}` defaults `{low_hz:30,high_hz:200,tilt_db:0}`;
20≤low<high≤20000, tilt -12..12. It is a planning preference, not fake EQ.

`annotations` is an array (max32) replacing the proposed map. Items:
`{id?,kind,label,x,y,mic_height_m?,orientation?}`. Kind is
`speaker|listening-area|fit|verify`; normalized image x/y 0..1 are NOT distances.
Optional existing IDs must already belong to this room; missing IDs are generated.
Label 1–80 chars. Fit/verify require mic_height_m 0.1..3 and orientation `0|90`.
Returned items always have stable IDs. Any photo/annotation edit clears
`annotations_confirmed`; `rooms.confirm` explicitly confirms the current version.
Photo replacement retains earlier private derivatives but invalidates map confirmation.

`fit_positions`: array 1..8 of `{position_id,weight}`; positive weight ≤100,
position IDs must name room fit annotations. `verify_positions`: array 1..8
of room verify annotation IDs. Disjoint and unique, each profile owns its sets.
Seat profiles require exactly one fit position; whole-room requires at least two.
Custom can use one. Whole-room/area/custom coverage is
explicit, never inferred from a photo. Profile records include room/map snapshot
hashes, `bindings`, `provenance`, `status`, `binding_valid`, `invalidated_reason`,
`analysis_available`, `proposal_available`, `applied:false`,
`verified_acoustically:false`, `sound_disabled:true`.

`bindings`: array max48 of `{position_id,channel,session_id,measurement_id}`;
channels left/right/sub. Partial binding is allowed: collect fit → analyze/propose
fit-only → collect held-out → review verification (never automatic success).
Each position × all three channels eventually
requires one matching imported `rew-response` with explicit `source:measured`,
metadata.position equal to stable position ID, matching annotation orientation,
sample_rate, calibration_sha256 and calibration_applied true, nonempty
timing_reference and explicit timing_quality. Bound sources must share sample rate
and per-channel calibration hash/microphone serial. Synthetic/unverified
sources are rejected, not promoted. No duplicate source or position/channel pair.
Read the existing sessions directory with anchored/no-follow private-file checks
(same existing 4MiB record bound), projected through application.session_view.
Fingerprints bind session measurement inputs (excluding derived analysis/proposal,
workflow state and sound_disabled) and the full source file, plus room/map and
owned profile plan. Thus analyze/propose alone do not invalidate unchanged imports.
Source changes, cancellation, map edits or lost confirmation cause
read-time deterministic invalidation; no stale analysis/proposal is exposed.
Binding imports does not analyze/propose/apply. Existing session analysis/proposal
can be referenced only when current and its entire named plan exactly matches
this profile's fit positions (verification sources stay held out); any weighted
plan cannot claim the existing unweighted engine honored weights. Nonuniform
weights expose `fitting_supported:false`,
`weighting_status:"unsupported-nonuniform-weights"`, no analysis/proposal reference,
and a guide action to choose uniform weights or external qualified fitting.
Availability
is reference availability, never acoustic verification. Guide names the first
missing position/channel, mic placement and REW metadata without issuing sound.

## Persistence and photos

Private generated-ID JSON state under root/room-domain, atomic fsync+replace,
anchored directory file descriptors with no-follow checks. Max128 rooms,
128 profiles, 512 photo derivatives, 64MiB aggregate, 1MiB JSON record. Limits
fail `storage_limit`; archive never deletes. Uploaded originals are not saved.
Under the existing Application thread/process lock, inventory recovers abandoned
`.write-<32 lowercase hex>` files only when private, current-user-owned, regular,
single-linked and ≤1MiB; empty/partial JSON is permitted for interrupted writes.
Recovery uses anchored no-follow descriptors and checks identity before deletion.
Symlinks, hardlinks, public/foreign/oversized files and unrelated names are rejected
and preserved. Never run inventory/save/list outside that lock.
If replacement commits but the subsequent directory fsync fails, return
`storage_durability_uncertain`; the caller must reload before retrying. No success
or crash durability is claimed, but already-referenced photo assets are retained.
Photo rollback is only for failures before the room replacement commits.
Strict base64 ≤700000 chars (<existing 1048576-byte HTTP body limit), decoded
≤525000 bytes, JPEG/PNG only, one frame, max4096 per axis/12M pixels.
EXIF orientation applied; pixels are rebuilt into metadata-free RGB JPEG ≤525000
bytes, bounded dimensions (max1600). No URLs/paths or provider uploads accepted.
Photo lookup must match owning room and generated asset ID; caller must authenticate
through existing HTTP/MCP boundary. No new unauthenticated asset route.

DomainFailure codes: `not_found`, `version_conflict`, `archived`, `storage_limit`,
`confirmation_required`, `source_invalid`, `photo_invalid`,
`storage_durability_uncertain`. Invalid IDs/schema/
position references are ValueError. No exception returns filesystem paths or content.

## Readback/guide lifecycle details

Profile projections always expose `fit_complete,held_out_complete,fitting_supported,
weighting_status,verification_status`. `status` is one of `archived`, `invalidated`,
`confirmation-required`, `unmeasured`, `collecting-fit`, `fit-imports-complete`,
`unsupported-nonuniform-weights`, `fit-analysis-available`, `collecting-held-out`,
`held-out-imports-complete`. Fit analysis/proposal reference availability does NOT
require held-out imports; held-out sources never enter the fit-only session.
`verification_status:"held-out-imports-ready-for-review"` is data readiness, not
measured improvement or independent acoustic verification.

Guide returns `{profile_id,status,sound_disabled:true,capture_available:false,
metadata_source:"measured",warning,action,...}`. Fit import actions include
`position_id,position,role:"fit",channel,required_metadata`. A compatible fit-only
session yields `action:"analyze-fit-only"` / `"review-and-propose-fit-only"` and
`operation:"calibration.analyze"` / `"calibration.propose"`, `args:{id}`,
`held_out_used_for_fit:false`. A fit proposal then prompts missing held-out imports
with `role:"verify-held-out"`; complete data prompts separate manual review.
No acoustic verification is computed. Nonuniform weights explicitly gate fitting.
Custom targets remain planning preferences; default-target references only, no
claim that the existing engine implements arbitrary target bands or tilts.
No auto-generated template is an AI result or measurement.
