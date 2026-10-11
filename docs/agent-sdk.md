# Official Agents SDK: build-only integration

Pinned `openai-agents==0.23.1`, Pillow `12.3.0`. Official reference:
https://openai.github.io/openai-agents-python/running_agents/ and
https://openai.github.io/openai-agents-python/sessions/.

**Production inference and vision are disabled unconditionally**, even with ambient
OpenAI credentials. No enable environment variable, model name, API key, external
photo upload, SDK cloud conversation or trace export is supported in this release.
ChatGPT subscription authorization does not authorize billed application API calls.
Future enablement requires a separately reviewed release, explicit spend/privacy
authorization and secure credential provisioning; no credential discovery is performed.

## Application adapter contract

Construct `application.agent_harness = Store(application.root)` from
`garage_audio.agent_harness`. Merge exported `SCHEMAS` and `OPERATION_SCHEMAS` into
strict shared validation/capabilities. Dispatch
`application.agent_harness.execute(operation, args, application)` under existing
Application thread/process locks; wrap returned raw values in the normal envelope.
Catch `AgentFailure` with existing domain exceptions (`code,message,requirements`).
No new authentication or unauthenticated route: retain HTTP Host/Origin, auth/CSRF
and SSH MCP controls. On process teardown call `close()` for the anchored store.

| Operation | Required | Optional | Result |
|---|---|---|---|
| agent.status | — | — | honest `inference_disabled` capabilities |
| agent.session.create | — | name,room_id | private persistent session |
| agent.session.get | id | — | current session and pending untrusted proposals |
| agent.run | id,expected_version,message | — | stores local user message; `state:inference_disabled`, `last_result.state:inference_disabled`; **no assistant reply** |
| agent.cancel | id,expected_version | — | cancelled session, proposals cleared |

IDs are generated 32-char hex. Names 1–120 chars, messages 1–8000; stale edits
return `version_conflict`. Private root/agent-sessions JSON has no-follow anchored
directory/file access, atomic fsync+replace, 0600 files; max128 sessions, 128 history
items and 256KiB/record. No originals/photos or secrets are read into SDK tools.

UI sequence: show disabled-state banner and manual/MCP alternative; session create
and reload are genuine local operations. Do not render canned/model-looking chat
as inference. Manual room annotation review uses `rooms.annotate` then an explicit
user action `rooms.confirm`, profiles use `listening_profiles.create` after review.
Model output, imported notes, or a text claim “user approved” never grants this
confirmation, measurement provenance, DSP application or sound authority.

## SDK-ready seam and isolated tests

`build_agent(application)` builds an actual SDK Agent with a `DisabledModel`, one
typed `function_tool` read boundary, and strict Pydantic `RoomAdvice` output.
Read allowlist: rooms.list/get and listening_profiles.list/get/guide, through the
same Application.execute validation. No generic mutation, confirmation, approval,
shell, network, arbitrary filesystem, photo bytes, device or sound tool exists.
Output supports multiple typed annotation and listening-profile suggestions. Image
coordinates are not distances; pending output is untrusted, never applied. Domain
validation and optimistic versions still apply when a user manually submits edits.

`LocalSession` implements the official SDK Session protocol over the same records.
`_run_isolated_fixture` is a test-only library seam, never operation/config/HTTP/MCP
dispatch. It uses actual Runner with a supplied scripted Model, max4 turns/default
(bounded1–8), timeout5s/default (≤30), cancellation propagation, sanitized failure
states, history rollback on failure, and both global/per-run disabled tracing.
Tests must block outbound sockets and supply scripted Models defined only in tests.
No synthetic fixture is installed as a production model/provider or exposed via UI.
Synthetic outputs are marked `synthetic:true`, `confirmation_required:true`.

This validates SDK orchestration only, **not live LLM or vision acceptance**. Real
SSH MCP, desktop LAN UI, physical-phone and sound/hardware evidence are separate.

## Dependency compatibility

The full lock installs and passes `pip check` on Python3.13 (existing
`music-assistant-client==1.6.0` requires Python≥3.13). MCP remains `1.30.0`, not
silently upgraded. SDK0.23.1 supports MCP≥1.19,<3 and Pydantic≥2.12.2,<3; existing
Pydantic2.13.5 is retained. OpenAI3.26.0/httpx2 dependencies coexist with MCP's
existing httpx0.28.1; requirements.lock pins the complete added dependency graph.
Pillow12.3.0 is installed for local safe photo parsing, not provider vision.
