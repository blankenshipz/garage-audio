# Garage Audio

**A self-hosted speaker system that keeps everyday listening simple and makes the
audio chain inspectable.**

Garage Audio connects Spotify on your phone to an amplifier and speakers through
a headless Linux server, a USB audio interface and software DSP. Alongside playback,
it provides a web dashboard and an agent-accessible workspace for speaker routing,
room layouts, listening profiles, measurements and experimental room correction.
Music Assistant handles the music; CamillaDSP handles the signal processing;
Garage Audio connects those pieces with shared controls and guarded device ownership.

## Why it exists

A DIY networked stereo can quickly become several unrelated systems: a music
receiver, a DSP configuration, a room-measurement tool and a collection of scripts.
Changing one leaves questions elsewhere: Which output is active? Which filters
will load next time? Does this curve describe a real measurement or just a prediction?

Garage Audio brings that work into one system without making it a prerequisite for
listening. Pick the speakers in Spotify and play music. When you want to investigate
the room or change the signal chain, use the same persistent workspace from a browser
or an MCP-connected agent. Keep original measurements, inspect proposed changes and
retain a path back instead of treating a promising graph as proof of better sound.

This is for self-hosters and audio experimenters comfortable operating Linux, Docker
and physical audio hardware. It is **not a turnkey receiver or an autonomous room
calibration appliance**. The reference stereo deployment has user-reported working
Spotify playback; newer subwoofer and correction workflows are still experimental.

## The everyday experience

Once the system is commissioned, the listening path is:

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

## What you can do today

**On the existing deployment:** listen through the Spotify stereo path on
commissioned OUT3/OUT4. Shared MCP/HTTP room-state readback has also been exercised.
This does not establish that every feature in the latest source is deployed.

**In the application workspace:** organize rooms, annotated photos and listening
positions; create independent listening profiles; import REW text responses;
analyze them and prepare conservative, cut-only EQ proposals. Measured inputs,
synthetic fixtures and electrical predictions remain distinct. Planning and
importing do not play sound.

**Implemented in newer source, awaiting physical acceptance:** persistent 2.1
routing and crossover configuration, correction prepare/apply/rollback/verify,
and a real ALSA microphone-capture adapter. Local tests and actual CamillaDSP
file-output renders exercise these software paths, not the room. Hardware
configuration apply remains blocked; file-only acceptance does not authorize it.

No qualified live microphone, commissioned subwoofer output or successful live
capture → correction → remeasurement result is established. UMIK-style measurement
is intended, not hardware-qualified. There is no claim of fully automatic bass
optimization, acoustic improvement or a clean-install production release. Query
the running server's capabilities rather than assuming source equals deployment.
See the [capability map](docs/publication.md) for exact boundaries.

## Before installing

**Never run test signals, increase amplifier gains or calibrate unattended.**
Missing hardware, authority or verified routing must fail closed.

The reference target is Linux amd64 with Docker Engine and recent Docker Compose
(overrides use `!reset`), trusted LAN access and SSH administration. Python 3.13+
is the application target; macOS software checks do not qualify Linux USB audio.

The audio configuration targets an identity-verified **Behringer UMC1820**. Its
observed Linux playback PCM is 12-channel, S24_3LE at 48 kHz; historical 20-channel
software templates are not evidence of the physical jack map. The commissioned
stereo uses OUT3/OUT4. Intended 2.1 wiring adds OUT5 to a powered subwoofer's
line/LFE input—**do not enable it before physical commissioning**. The reference
speakers are Fosi V3 amplification, Klipsch R-28F mains and an R-12SW sub; these
speaker models are not themselves prerequisites for other safely commissioned rigs.

Music Assistant onboarding and Spotify terms, OAuth or provider-key steps belong
to the human account owner. Automated measurement additionally needs a compatible
ALSA microphone with verified identity, orientation and calibration—not simply an
available capture input. REW text import is the alternative for external measurements.

## One-time setup

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
5. Finish MA/Spotify onboarding once, verify the exact player and physical map with
   low fixed amplifier gains, and retain a known-good rollback. Open the dashboard
   at your configured host's port 8098 on trusted Wi-Fi only. HTTP is unencrypted;
   never publish it to the Internet or proxy the private DSP websocket.

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

The correction workflow is designed around evidence, not a one-click “optimize”:

1. **Plan:** record channels, listening positions, microphone orientation and
   calibration provenance. Read readiness before requesting acquisition.
2. **Measure:** import qualified REW responses, or use hardware capture only when
   the server reports it supported and ready. Capture needs a root-pinned microphone
   policy, exclusive device ownership and independent human approval of the exact
   bounded test. Public/container human approval is not currently available.
   Agents cannot self-attest presence or approve their own tests.
3. **Improve:** inspect capture quality, analyze and prepare conservative cuts.
   Unknown timing does not justify inferred delay. Review the exact proposed change;
   preparing it is not permission to apply it to hardware or start sound.
4. **Verify:** compare matching, newly acquired after measurements. A predicted
   curve or successful file render is not measured improvement.
5. **Keep or roll back:** retain original measurements and configuration history.
   Cancel stops owned test work; stale proposals require fresh readback and validation.

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
