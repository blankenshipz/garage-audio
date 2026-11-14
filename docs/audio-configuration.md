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
cabling/powered-sub compatibility. The hardware layout instead follows the root-owned
device policy (see [Powered sub on OUT5](#powered-sub-on-out5)).

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

Both FILE and hardware startup compile accepted routing/correction. Before device
admission, `hardware_graph` requires the compiled route to equal the device policy's
`outputs` exactly; any other route is rejected, not silently truncated. FILE state
cannot migrate to hardware because binding differs.
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

## Powered sub on OUT5

The root-owned policy mounted at `/device-policy.json` (host
`/etc/garage-audio/docker-device.json`) admits exactly two layouts
(`audio_device.load_policy`):

| `outputs` | Hardware graph |
|---|---|
| `[3, 4]` | Stereo, full range on OUT3/4. |
| `[3, 4, 5]` | `powered-sub-2.1`: OUT3/4 LR4 high-pass at 80 Hz; OUT5 gets L+R at −6.02 dB each, LR4 low-pass at 80 Hz. |

Headroom stays −6 dB in both. Because hardware apply is unavailable, the worker's
hardware configuration starts from the policy (`audio_config.default_configuration`),
not from a proposal. A saved hardware configuration whose route differs from the
policy fails closed at start. Status `outputs` reports the loaded route.

The policy is an operator wiring statement, not acoustic verification. Wire OUT5
to the sub's line/LFE input with a balanced-to-unbalanced 1/4-inch-to-RCA lead, sub
gain at minimum and its own low-pass fully open. Then:

- Write the policy with `scripts/bootstrap-container-audio.py --apply --sub-output …`
  (without `--sub-output` it writes stereo), or edit `outputs` in place (root, 0444).
- If the running audio-device or dashboard image changes, update its image ID and
  the hashes of any changed frozen inputs in `/etc/garage-audio/audio-boot.json`;
  `audio_boot.load_config` refuses mismatches.
- Restart `garage-audio-network`. The next Play loads the 2.1 graph.

To roll back, write `outputs: [3, 4]` and restart. Set the sub's level and phase by
measurement.

