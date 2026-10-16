# Garage Audio

**Build a self-hosted music and DSP system—with a web workspace and agent tools
to help configure it, understand your room and work toward better sound.**

Garage Audio brings Music Assistant, CamillaDSP and Linux audio together behind
shared web and MCP controls. Connect a server to your amplifier and speakers through
a USB audio interface, choose your music source, then manage routing, listening
profiles and measurements in one place. An MCP-connected agent can inspect the
system, help prepare DSP changes and guide a measurement-and-correction workflow
without replacing the music app you use every day.

## Why it exists

A DIY networked stereo can quickly become several unrelated systems: a music
receiver, a DSP configuration, a room-measurement tool and a collection of scripts.
Changing one leaves questions elsewhere: Which output is active? Which filters
will load next time? Does this curve describe a real measurement or just a prediction?

The useful part is the connection between those tasks. Ask an agent why a player
is silent, have it read the actual readiness state, then inspect the same result in
the dashboard. Import a room measurement, discuss the peaks and prepare a bounded
EQ proposal while keeping the original data and a rollback path. Configuration
becomes an inspectable workflow rather than a pile of disconnected settings.

This is for self-hosters building a garage stereo, workshop system or listening-room
experiment who are comfortable with Linux, Docker and physical audio hardware.
It is an early-stage integration project, **not a plug-and-play receiver or a fully
autonomous calibration appliance**. Spotify stereo is the demonstrated playback
path; broader provider integration and physical room correction need further work.

## Why build this instead of using an AVR?

The advantage is **programmability and control, not more amplifier watts or a
promise of better sound**. A traditional audio/video receiver packages a great deal
into one appliance. Garage Audio instead exposes the pieces so you can inspect,
adapt and automate the supported workflow rather than stay inside one receiver's
menus and integration choices.

| What you want to control | What the open stack gives you |
|---|---|
| Signal routing and DSP | Inspectable channel maps, headroom, crossovers and cut-only EQ proposals; source code and configuration you can extend rather than a fixed set of presets. Hardware changes still need validation and commissioning. |
| Music sources | A Music Assistant backend separate from the DSP chain, so provider integration does not require replacing your amplifier or EQ engine. Individual source paths still need qualification. |
| Experiments and profiles | Retained measurements, explicit configuration revisions, proposal diffs and rollback workflows—a record of what changed and why, not just a remembered knob position. |
| Automation and assistance | A shared API/MCP workspace where an agent can diagnose prerequisites, organize measurements and prepare changes you can review in the browser. Sound authority remains separate. |

The tradeoff is that **you own the integration**: Linux maintenance, device policy,
cabling, amplification and verification. Many AVRs already offer sophisticated
room correction, network control and convenient calibration; this project does not
claim to outperform them acoustically. An AVR may be the better choice for integrated
amplification, HDMI switching/eARC, surround decoding and appliance-like operation.
Garage Audio is **not a full home-theater AVR replacement**. Choose it when the
ability to understand and extend your music/DSP system is itself part of the goal.

## Build your own system

Start with a working stereo, then add DSP experiments and measurement. You do not
need a subwoofer, microphone or agent just to listen.

| Piece | What to plan for |
|---|---|
| Host | Linux amd64, Docker Engine, recent Docker Compose (`!reset` support), persistent storage, trusted LAN and SSH access. Python 3.13+ is the application target. |
| Audio interface | The implemented device policy targets a Behringer UMC1820 with a verified USB identity and channel map. Another DAC requires device-policy/routing adaptation and commissioning; arbitrary USB audio is not plug-and-play. |
| Speakers and amplification | A compatible amplifier and passive speakers, or suitable powered speakers, with correctly matched line-level connections and accessible physical volume/mute controls. |
| Optional subwoofer | A powered sub with a compatible line/LFE input and a separately commissioned output. Plan the main high-pass/sub low-pass crossover before enabling 2.1. |
| Optional measurement kit | A calibrated microphone, its matching calibration file and repeatable placement. REW text import supports measurements made externally; automated ALSA capture has additional hardware/approval prerequisites. |

The browser/MCP workspace, room records and REW analysis are not tied to the author's
speaker models. **The hardware output policy is device-specific.** Treat the current
interface and wiring as a reference integration to adopt or deliberately port, not
as a generic sound-card autodetector.

### Your path from hardware to music to DSP

1. **Map the physical system.** Identify the interface, left/right outputs, amplifier
   inputs and safe gain settings. Leave any uncommissioned sub output disabled.
2. **Install the software and private state.** Follow [one-time setup](#one-time-setup)
   to review host device isolation, choose matching images and connect the dashboard
   to Music Assistant. This still needs operator setup, not just `docker compose up`.
3. **Choose and authorize a music source.** Configure it in Music Assistant using
   the provider's own account/consent process. Source selection and speaker-output
   readiness are separate; a populated music library does not prove audio can play.
4. **Establish quiet stereo playback.** Commission the exact outputs with low
   physical gain, then verify Play, Pause, Stop and reconnect. Spotify Connect is
   the project's demonstrated route; qualify a different provider before relying on it.
5. **Prepare your DSP configuration.** Inspect the active routing and headroom;
   prepare a stereo or future 2.1 proposal, review its diff and validation, and retain
   the previous configuration. Current hardware apply restrictions still apply.
6. **Measure before correcting.** Create a room/listening plan, import measurements,
   analyze and propose conservative cuts. Only call a change an improvement after
   matching new measurements—not because its predicted curve looks better.

### Choose a provider, not a different DSP stack

Music Assistant separates **where music comes from** from **which player receives
it**. Its upstream [music-source catalog](https://www.music-assistant.io/music-providers/)
includes local files, media servers such as Jellyfin/Plex, and streaming providers
such as Spotify, Qobuz and Tidal. Configure your chosen source in Music Assistant's
music-source/provider settings and follow the instructions for your installed version;
subscriptions, authentication and features vary by provider.

Garage Audio's output side is Squeezelite → PipeWire → CamillaDSP → ALSA. That
separation is the integration point for other sources: you should not need a new
EQ engine for each music service. **It is not yet a compatibility guarantee:**
those upstream providers have not all been qualified through this project's guarded
playback lifecycle. Adding a provider does not automatically arm the DAC or make
agent-initiated Play available. For another source, first verify the selected MA
player, admission/stop behavior, volume handling and reconnect on your installation.

Spotify has two distinct roles in Music Assistant:

* A **Spotify music source** supplies library/playback access inside Music Assistant.
* The **Spotify Connect plugin**, using Soloist on the documented path, advertises
  the selected MA player in the Spotify app. It is not another local audio transport;
  Squeezelite remains the player feeding the DSP chain.

The Garage Audio setup workflow focuses on the latter. Other provider onboarding
belongs in Music Assistant; the dashboard is not a universal provider wizard.

## What an agent can help you do

Connect your preferred MCP-capable assistant to Garage Audio's server. Instead of
asking it to guess a CamillaDSP file, let it discover the deployed capabilities,
read current state and use typed operations. You can inspect its rooms, plans and
proposals in the same web workspace and return to them after reconnecting.

These are illustrative prompts for an **external MCP-connected agent**, not canned
features of the dashboard's chat tab:

| Ask your agent | Useful work through Garage Audio |
|---|---|
| “Inspect this installation. What is ready, and what is missing before I can listen? Don't start sound.” | Read status/capabilities and report the server's device, backend and credential prerequisites—not guess what is physically plugged in. |
| “Explain my left/right routing and headroom. Prepare a 2.1 proposal for review, but don't apply it.” | Inspect configuration, propose supported routing/crossover changes and show validation/diffs. A proposal cannot commission an output. |
| “There is no sound. Check the player and audio readiness before suggesting a restart.” | Read available MA/player and audio status, distinguish missing prerequisites from playback state, and recommend the next targeted check without unmuting or bypassing DSP. |
| “Create a room plan for my workbench and listening chair. Tell me what to measure at each position.” | Create room/listening-profile records and a placement/channel guide; keep fit and held-out verification positions distinct. |
| “Analyze these REW exports. Which peaks are candidates for conservative cuts? Keep the originals.” | Import labeled responses, analyze them, prepare cut-only proposals and explain predictions and data-quality warnings. |
| “Show the exact correction, its prerequisites and rollback. Apply only if this deployment supports it and the required approval exists.” | Prepare/read the correction and report whether apply is available. After a supported change, guide remeasurement, verification and rollback rather than claiming success from a saved setting. |

The agent coordinates the workflow; **you wire the system, place and calibrate the
microphone, authorize accounts and attend any sound-producing test**. It cannot
infer physical distances from a room photo, self-approve a test or turn a synthetic
render into a room measurement. Hardware apply and end-to-end acoustic correction
remain unaccepted; the useful planning, import and analysis work does not depend on them.

The embedded OpenAI Agents SDK scaffold currently has inference disabled. Use an
external MCP client for this assistance, or use the manual dashboard without an
agent. See [agent connection](#connect-an-agent) for the client configuration.

## The everyday experience

For the commissioned Spotify Connect path, the listening experience is:

1. Open Spotify on a phone on the same trusted Wi-Fi.
2. Select **Garage Speakers** and press **Play**.
3. Use Spotify to pause, resume or stop. Dashboard and MCP controls also expose
   status and silence controls; neither is required to start ordinary listening.
4. Close and reopen Spotify to return to the same receiver. After a reconnect with
   stale playback state, Pause then Play creates a fresh playback transition.

There is no daily terminal command, Music Assistant session button or expiring
calibration approval to renew. Provider onboarding is a one-time setup concern;
measurement permissions are separate from normal playback.

For the reference fixed-level stereo setup, the amplifier knob is the primary
volume control; Spotify supplies optional digital attenuation. Start with the
amplifier turned down and raise it slowly. DSP headroom is not a safe-loudness
guarantee. See the [Spotify quickstart](docs/spotify-only-quickstart.md).

## How it fits together

The audio path and the control workspace are separate:

```text
AUDIO
Spotify phone
    │ Spotify Connect
    ▼
Music Assistant + Soloist → Squeezelite → private PipeWire stream
                                                │ PCM
                                                ▼
                                  guarded audio owner + CamillaDSP
                                                │ ALSA / USB
                                                ▼
                                      Behringer UMC1820
                                                │ analog outputs
                                                ▼
                                      amplifier → speakers

CONTROL
Web dashboard ───────────────────┐
                                 ▼
MCP client → SSH → MCP adapter → shared Python application → private room/profile state
                                 │
                                 ├→ Music Assistant API
                                 └→ guarded audio control boundary
```

* **Music Assistant is the music backend**, providing the Spotify Connect/Soloist
  integration and player/queue APIs. Squeezelite is the player transport, not a
  second Spotify receiver. Spotify is the documented everyday source here; Music
  Assistant's wider provider support is not a claim that every source is qualified.
* **PipeWire and CamillaDSP form the audio pipeline.** A private PipeWire stream
  feeds PCM into the owned DSP process, which handles routing, gain and filters
  before ALSA opens the explicitly identified USB interface.
* **Garage Audio owns the coordination and safeguards.** Device identity, playback
  transitions and process supervision prevent an unavailable path from silently
  switching to HDMI, internal audio or an unfiltered output. The deployed baseline
  uses a custom Music Assistant lifecycle bridge; the Docker-first design moves
  that coordination into dedicated audio services. These are distinct deployment
  paths, not interchangeable Compose recipes.
* **The web UI and MCP use one application and one set of records.** An agent can
  create a room or calibration plan and you can inspect that same record in the
  dashboard. MCP is authenticated stdio over SSH, not a public agent endpoint or
  arbitrary shell. The dashboard gets neither direct DAC access nor a Docker socket.

### Technology stack

| Layer | Technology | Role |
|---|---|---|
| Application | Python 3.13+, standard-library HTTP server, JSON Schema | Shared typed operations, validation and persistent workflow state |
| Web interface | Plain JavaScript ES modules, HTML and CSS | Dependency-free dashboard, plots, setup and measurement controls |
| Agent interface | Official Python MCP SDK / FastMCP, SSH stdio | External agents access the same authenticated application |
| Music backend | Music Assistant, Soloist, Squeezelite | Spotify Connect, queues and decoded audio transport |
| Audio graph | PipeWire, WirePlumber, ALSA | Private stream routing and explicit Linux device access |
| DSP | CamillaDSP and its Python client | Channel routing, headroom, crossover/filter graphs and offline renders |
| Deployment | Docker Compose, Linux host device policy and service tooling | Separate application/audio roles and persistent private state |
| Embedded agent scaffold | OpenAI Agents SDK | Local session/tool plumbing; **model and vision inference are disabled** |

The stack is defined in [Python dependencies](requirements.txt), the
[audio image](Dockerfile.audio), [Compose configuration](compose.yaml) and
[frontend guide](web/README.md). The embedded chat scaffold is not a working AI
assistant: it makes no model calls, even with ambient credentials. External MCP
clients remain usable independently; Garage Audio does not require paid inference
for listening, manual controls or measurement planning.

## Reference hardware and project maturity

The reference system uses a UMC1820, Fosi V3 amplification and Klipsch R-28F mains;
an R-12SW powered sub is the intended 2.1 addition. Stereo OUT3/OUT4 has user-reported
working Spotify playback. OUT5 is intended for the sub's line/LFE input and is **not
commissioned**. The observed Linux playback PCM is 12-channel S24_3LE/48 kHz;
historical 20-channel software templates do not establish the physical jack map.

Room/profile storage, REW import and analysis, and shared MCP/HTTP state are
implemented. Newer source adds persistent 2.1 configuration, a real ALSA microphone
adapter and correction prepare/apply/rollback/verify. Local tests and actual
CamillaDSP file renders exercise those software paths; they are not acoustic proof.
Hardware configuration apply remains blocked, no qualified live microphone is
established, and no live capture → correction → remeasurement improvement is claimed.
UMIK-style capture is intended, not hardware-qualified.

The latest source and the running deployment may differ. Discover actual capabilities
before acting; a successful build is not a clean hardware installation or a verified
USB-fault recovery. See the [capability map](docs/publication.md) for details.

## One-time setup

**Never run test signals, increase amplifier gains or calibrate unattended.**
Missing hardware, authority or verified routing must fail closed. For a new build,
complete the host/device prerequisites before activating the audio stack; software
checks on macOS do not qualify Linux hardware.

1. Clone the repository and review [Docker installation](docs/docker-install.md)
   and [device isolation](docs/dac-isolation.md). Existing installations must use
   the lifecycle owner's reviewed update/rollback procedure, **not** an ad-hoc
   `docker compose up` against live state. Native service definitions are not a
   generic fresh-install script; host ownership and identity need operator review.
2. Copy `.env.example` to private `.env`. Replace example persistent directories,
   LAN IP and exact authorities; select matching reviewed immutable dashboard and
   audio images. Retain a separate loopback binding. The example IP is deliberately
   non-routable documentation space. Existing Compose defaults are preserved.
3. Provision private dashboard auth/data and the server-side MA token reference as
   described in [dashboard access](docs/dashboard.md). Keep credentials out of
   Git, `.env`, chat, MCP arguments and logs. Do not regenerate working credentials
   just to upgrade. The dashboard has no Docker socket or direct DAC device access.
4. Validate configuration without starting anything:

   ```sh
   docker compose --env-file .env -f compose.yaml -f compose.dashboard.yaml \
     -f compose.audio.yaml config --quiet
   ```

   Missing image variables are actionable prerequisites, not permission to use an
   unrelated cached image. Review [container topology](docs/container-audio.md)
   and hardware/boot overlays before any operator-authorized activation.
5. Finish your chosen MA/provider onboarding once, verify the exact player and
   physical map with low fixed amplifier gains, and retain a known-good rollback.
   Open the dashboard at your configured host's port 8098 on trusted Wi-Fi only.
   HTTP is unencrypted; never publish it to the Internet or proxy the private DSP websocket.

## Connect an agent

Use an MCP client with the SDK stdio transport over your existing SSH identity:

```json
{"command":"sh","args":["/absolute/path/to/garage-audio/scripts/dashboard-mcp"],"env":{"GARAGE_HOST":"operator@audio-host"}}
```

The wrapper uses `ssh -T` and `docker exec -i`. Credentials stay server-side; do not
copy the dashboard token into client configuration. Initialize, list tools and
resources, read `garage_status`/`garage_capabilities`, then follow the exact returned
schemas. The generic `garage_operation` exposes supported typed operations, not
shell execution. Reconnect and rediscover after an update. Full onboarding,
actionable errors and authority boundaries: [agent guide](docs/agents.md).

## Measure → improve → verify → rollback

For example, suppose bass sounds boomy at your workbench but thin at a nearby chair.
Rather than asking an agent to “fix the bass” from that description alone:

1. **Plan the question.** Ask it to create a room record and listening plan for those
   positions, identify the channels to measure and retain separate verification
   positions. You confirm the physical layout and microphone placement; a photo
   annotation is not a distance measurement.
2. **Bring real data.** Take attended measurements with a calibrated microphone and
   export REW text responses. Ask the agent to import each with its channel, position,
   sample rate and calibration provenance. Inspect the same curves in the dashboard.
   Automated capture is only an option when the server reports hardware readiness:
   it needs root-pinned microphone policy, exclusive ownership and independent approval
   of the exact bounded test. Public/container human approval is not currently available.
3. **Review a correction, not a promise.** Ask for an analysis of the imported peaks
   and conservative cut-only EQ. Inspect the proposed filters and electrical preview.
   Do not boost a null or infer delay from data with unknown timing. A useful result
   may be “leave this unchanged” rather than inventing an improvement.
4. **Prepare and apply only where supported.** Have the agent show the exact prepared
   correction, current configuration and rollback path. It must report missing
   authority rather than treating your chat reply as a sound grant. Current file-only
   apply acceptance does not authorize applying to your speakers or starting playback.
5. **Remeasure and decide.** After an authorized physical change, acquire matching
   new measurements and compare them using the verification workflow. Keep the change
   only on actual evidence; otherwise use the supported, freshly validated rollback.
   Originals and history remain available. Cancel stops owned test work, not history.

Subwoofer routing follows the same principle: a saved crossover or trim does not
verify a cable, commission a jack or prove better bass. See
[audio configuration](docs/audio-configuration.md),
[measurement workflow](docs/measurement-workflow.md) and the
[API contract](API_CONTRACT.md) before using these experimental paths.

## Recovery

Use Stop first. Read status and its exact error; preserve private logs and original
measurement/configuration evidence. Missing microphone/calibration needs physical
setup; `human_approval_required` needs independent approved authority; version or
generation conflicts need fresh readback/replanning. Never retry old grants.
Restart invalidates nonterminal tests and cannot authorize automatic test sound.

An owner/receiver failure may intentionally leave hardware OFF with DAC closed.
Do not repeatedly restart individual containers: generation guardians bind the
combined lifecycle. Use [Docker rollback](docs/docker-rollback.md) and the lifecycle
owner's targeted repair. No prune, state-volume deletion or unrelated service restart.
See [agent recovery](docs/agents.md) for reconnect and structured failures.

## Develop and package

Use Python 3.13+ in a virtual environment, then install `requirements.lock` for the
reviewed dependency set and `pip install --no-deps -e .` for source development.
The application remains source-layout aware; a wheel bundles UI/profile assets at
`<venv>/share/garage-audio`. For a wheel installation, pass that explicit `--web`
directory to `python -m garage_audio.webserver`; legacy host-service tooling still
requires its reviewed source checkout and is not enabled by `pip install`.

```sh
python -m pip install build==1.2.2.post1
python scripts/publication-check.py --output /absolute/private/receipt-directory
node --test web/tests/*.test.js
```

The publication check builds twice from a frozen source snapshot, compares wheel
hashes and normalized source inventories, verifies required assets/vendor notices
and rejects private state/cache inputs. Full logs belong outside release inputs.
CI runs software-only packaging/UI tests, never opens an audio device or publishes.
Clean-room dependency/image builds, owner-aware deployment and real hardware gates
remain separate. A source package is not a commissioned audio appliance.

## Further reading

* [Everyday Spotify listening](docs/spotify-only-quickstart.md)
* [Installation and deployment](docs/docker-install.md) · [Container audio topology](docs/container-audio.md)
* [Dashboard access](docs/dashboard.md) · [MCP agent guide](docs/agents.md)
* [Room/profile API](API_CONTRACT_ROOM.md) · [Measurement workflow](docs/measurement-workflow.md)
* [Capability and publication boundaries](docs/publication.md)
* [Architecture and roadmap](docs/architecture-roadmap.md) · [Experimental active-control research](docs/active-control-research.md)

Some operational documents retain historical deployment notes. Follow their stated
scope; they are not commands to replay against a live installation.

## License

Original code is [MIT licensed](LICENSE). Bundled components retain their own
licenses; see [third-party notices and source provenance](THIRD_PARTY_NOTICES.md).
