# Garage Audio

Headless Spotify playback and a shared web/MCP workspace for safe speaker routing,
subwoofer configuration, measurement and conservative room correction.

**Never run test signals, increase amplifier gains or calibrate unattended.**
Missing hardware, authority or verified routing fails closed; there is no default
sound-device fallback. Original code is [MIT licensed](LICENSE); bundled software
retains [its own terms and source provenance](THIRD_PARTY_NOTICES.md).

## What works—and what is not yet verified

The existing deployment has user-reported working Spotify playback after its
focused device repair. Source adds persistent 2.1 routing, cut-only correction,
rollback and a real ALSA microphone-capture adapter. Actual CamillaDSP FILE renders
and local lifecycle/MCP/UI tests are software evidence, **not acoustic proof**.

The deployed baseline and latest source are not interchangeable: the baseline MCP
inventory was 34 tools, five resources and 81 operations; six lifecycle tools and
seven operations exist in the newer candidate but await owner-aware activation.
Always discover actual server capabilities. A built image is not an active release.

No qualified microphone is currently established, subwoofer output commissioning
and hardware apply authority remain pending, and no live capture→correction→
remeasurement acoustic improvement is claimed. Source packaging and CI do not prove
a clean hardware installation, physical-phone UI or recovery after a real USB fault.

## Supported target and prerequisites

* Linux amd64, Docker Engine and recent Docker Compose (overrides use `!reset`).
  Python 3.13+ is the application target. macOS can run local software checks, not
  qualify the server's USB/audio hardware.
* Behringer UMC1820 with an explicitly verified USB identity. The observed Linux
  playback PCM is 12-channel, S24_3LE/48 kHz; do not use historical 20-channel
  software templates as evidence of the physical jack map.
* Existing commissioned mains: OUT3/OUT4. Intended 2.1 wiring adds OUT5 to a powered
  subwoofer's line/LFE input; **do not enable it before physical commissioning**.
  The reference setup uses Fosi V3, Klipsch R-28F mains and R-12SW sub; those speaker
  models are not inferred prerequisites for other safely commissioned systems.
* Automated acquisition requires a compatible ALSA microphone, verified serial,
  orientation and calibration file. UMC capture inputs alone do not establish a
  calibrated microphone. UMIK-style USB measurement is intended but not currently
  hardware-qualified. Laptop REW text import remains available.
* SSH and trusted LAN access; human-owned Music Assistant onboarding, Spotify
  provider terms/OAuth and any required provider key. No API-key inference or paid
  model call is required. Embedded model inference is disabled.

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

## Everyday listening

After commissioning: **Spotify → Garage Speakers → Play**. Start quietly; use
Pause/Resume and Stop in Spotify. Stop/pause/status also have dashboard/MCP paths.
Close and reopen Spotify to reconnect to the same receiver. See
[Spotify quickstart](docs/spotify-only-quickstart.md).

Ordinary playback is separate from one-time provider onboarding and expiring
measurement grants. A cancelled or expired test must not require daily re-enrollment
or a new dashboard token. Do not use historical attendance-string commands to
authorize sound. A muted/unavailable device requires the reported readiness repair,
not fallback to HDMI, internal audio or an unfiltered bypass.

For subwoofers, review [audio configuration](docs/audio-configuration.md): route only
commissioned outputs, retain main high-pass/sub low-pass crossover and conservative
headroom, then adjust bounded sub trim, mute or polarity deliberately. Persistent
configuration is applied for subsequent playback; saving a route does not verify
its cable, authorize an uncommissioned jack or demonstrate bass improvement.

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

1. Read readiness, including `measurement.hardware.status` when advertised. Create
   a quiet calibration plan with explicit channels, positions, mic orientation,
   sample rate and calibration provenance. Planning makes no sound.
2. Import qualified REW responses, or request `jobs.request` with `adapter:"hardware"`
   **only when supported and ready**. Hardware capture uses root-pinned
   `/etc/garage-audio/measurement.json` and microphone calibration, isolated device
   ownership and a private exclusive lease. Independent human exact-manifest
   approval, bounded output and cancellation remain mandatory; MCP cannot invent
   presence or approve itself. Public/container human approval remains unavailable.
3. Inspect capture QC before `calibration.capture.import`; synthetic FILE/null jobs
   cannot be promoted to measured evidence. Analyze, propose conservative cuts,
   prepare and read the correction. Unknown timing does not justify inferred delay.
4. `calibration.correction.apply` accepts only a supported, exact prepared state;
   current FILE acceptance is not hardware apply authority and never starts sound.
   Use rollback on conflict/failure rather than overwriting a newer configuration.
5. Obtain matching newly acquired after measurements, then
   `calibration.correction.verify`. Pairing/configuration/provenance must match;
   synthetic results and predicted curves are not acoustic verification. Cancel an
   active job to stop its owned work; rollback retains original measurements.

See [measurement details](docs/measurement-workflow.md), [API](API_CONTRACT.md), and
[current source capability matrix](docs/publication.md). Hardware acquisition,
physical sub commissioning and acoustic apply/verify remain explicit acceptance
gaps, not instructions to bypass safeguards.

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
remain separate. Historical operational evidence lives in `docs/`, not quickstart
commands to replay blindly. No public push or release is implied by this repository.
