# Everyday Spotify playback

Once deployment is armed, use **Spotify only** on a supported phone on the same
trusted home Wi-Fi: choose **Garage Speakers**, then press **Play**. No Music
Assistant session button, browser code, terminal or periodic approval is required.

For the first new fixed-level playback, turn Fosi fully down first. After you hear
music starting, raise its physical knob slowly. Fosi is the primary volume control;
Spotify volume is optional digital attenuation. OUT3/4 are full-range stereo with
unity DSP master and one −6 dB headroom stage. There is no sub/crossover requirement.
Digital headroom does not guarantee safe acoustic level or prevent analog clipping.

Pause mutes output. Resume starts quietly with the current chosen digital level;
after ten minutes paused, the native device closes and a fresh Spotify Play can
prepare it again. Stop, receiver/listener loss, process/reference drift or a DSP
guard fault closes the native output; the persistent player remains registered.

Boot/reconnect never authorizes old PLAYING snapshots. If Spotify was already
playing during a reconnect, use Pause then Play once to create a fresh transition.
This is not a listening-session approval ceremony. No agent selects the receiver,
logs in, presses Play or provides physical-volume confirmation for you.

## Deployment status

The source defaults to **disabled** for automatic sound. “Disabled” in the MA
speaker-control status is not “ready.” Root deployment must install the exact
native/provider pins, protected receiver port, approved Soloist digest and supported
`sync_spotify` configuration. Root arms only after the first-level confirmation;
that administrative flag does not play cached music or grant an existing snapshot.
The optional manual controls in MA remain separate for diagnostics/other sources.

Same-LAN guest/cross-account device visibility and actual phone playback/control
must be verified with the real phones. Desktop discovery, HTTP101 and FILE render
tests are not physical-phone or acoustic evidence. Check Spotify’s device picker
and, on iPhone, the Spotify Local Network permission if the receiver is absent.
