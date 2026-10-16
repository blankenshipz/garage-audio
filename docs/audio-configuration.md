# Configuration milestone (private alpha)

Setup contains an Audio configuration panel. `audio.config.get`, `.propose`,
`.validate`, `.apply`, and `.rollback` use the same strict app/MCP schemas. Named
SDK tools expose these operations without raw DSP, paths or arbitrary argument JSON.

Proposals carry exact content identity, base revision, compiler/binding/capability
revisions and a before/after diff. Validate the exact proposal before accepting it.
Rollback requires a **fresh proposal** of the previous accepted configuration and
the same validation and authorization rules. Stale proposals cannot apply.

Only a server-owned, declared virtual FILE binding can apply in this milestone.
Hardware apply remains blocked: no trusted-LAN session, browser button, chat “yes”
or agent assertion provides independent exact-proposal human authority or verifies
cabling/powered-sub compatibility. Production stereo remains OUT3/4 at −6 dB.

## Runtime correction contract

Compiler `layout-cut-peq-v2` retains the existing configuration fields and adds an
optional `correction` object with exactly `left`, `right`, `sub` arrays. Each has
at most four `{freq,q,gain}` filters: 20–1000 Hz (sub: 20–300), Q 0.5–10,
gain −6–0 dB. Boost, extra fields, nonfinite numbers and sub filters without a
routed sub are rejected. Omission means no correction, preserving old clients
and state. These are electrical declarations, not measurement provenance.

Use existing `audio.config.propose {configuration}` → `.validate {proposal_id}` →
`.apply {proposal_id}`. The exact correction is included in proposal identity,
diff, persistent accepted state and next-start graph. Removing correction appears
in the diff too. `.rollback` requires a freshly validated proposal of the complete
previous state, including its correction. Readback adds
`correction_verification: electrical-declaration-only-not-acoustic-proof`.

Both FILE and hardware startup compile accepted routing/correction. Hardware still
enforces commissioned OUT3/4 before device admission; a 2.1 route is rejected,
not silently truncated, until independently reviewed wiring policy and authority
are implemented. FILE state cannot migrate to hardware because binding differs.
Hardware apply/rollback/validation remain denied; this checkpoint adds no authority
channel or device opens. All sound still requires fresh receiver admission and
existing network/device/watchdog safeguards. No acoustic verification is implied.

Acceptance is **for next Play**, not a playback command. Desired/accepted state,
validation, process-launched loaded state, and physical verification are distinct.
The worker does not claim DSP readback from process launch. FILE measurements are
synthetic electrical software proof, not room improvement or hardware proof.
Stop bypasses app persistence and long worker command locks. Staging/fsync cannot
delay Stop's state transition; its generation prevents a later interrupted commit.

Inference stays unconditionally OFF before credential lookup/provider creation.
Named tools are implemented; an actual model-agent journey still requires separate
provider/budget authorization; the app now enters the guarded runner outside its
global persistence lock, and the unconditional gate prevents all provider work. No canned reply is
presented as a model result. This remains a private alpha, not a portability,
license-completeness or novel-acoustic-algorithm claim.
