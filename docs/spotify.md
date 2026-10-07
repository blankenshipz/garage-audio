# Spotify — user-owned account/consent; topology follow-up required

Official docs checked 2026-10-06:
- https://www.music-assistant.io/plugins/spotify-connect/
- https://www.music-assistant.io/music-providers/spotify/
- https://www.music-assistant.io/player-support/squeezelite/

Spotify **music source** and **Spotify Connect plugin** are different. The plugin makes the selected native MA player appear in the official Spotify app; it does not create a second local player transport. Keep Squeezelite as the one output transport.

## Current requirements (not account assumptions)

Connect plugin offers:
- **Spotify Soloist (official, recommended in current docs):** Premium needed initially to create a personal Soloist API key; subsequently Free/Premium accounts can connect. User must read and accept download/Spotify terms and personally create the key/pair in Spotify. Docs warn terms do not clearly permit MA's Soloist usage; no consent has been accepted on your behalf. Engine is downloaded after consent and auto-updated by MA, so its exact version cannot yet be pinned/verified here.
- **go-librespot (community):** no Soloist API key/download, intended for Premium accounts created before December 2024; compatibility can change. Account age/status has not been collected or assumed.

Spotify music-source provider instead requires Premium, supports librespot community or Soloist, uses OAuth plus separate playback approval/pairing. Do not confuse source OAuth with Connect plugin authorization.

## Material deployment decision

Current MA uses a isolated **Docker bridge**, loopback-published UI/stream/Slimproto ports. Official Spotify source docs explicitly state **Soloist cannot be used on hosts with Docker bridge networks**. LAN Connect discovery is also not proven through the bridge/tunnel. Therefore Spotify is **not configured or claimed functional**. Do not silently pick the older-account community engine to evade this.

After user engine choice/consent, a scoped host-network MA deployment may be needed. Before any change: inspect all required ports (8095/8097/3483 and any engine discovery ports), configure authenticated UI/explicit bind addresses, disable unused Squeezebox CLI ports/discovery, preserve scanner host-network workloads, and verify actual phone LAN discovery. No host firewall/SSH/network changes are authorized by this project. Parent/user must decide this topology in the follow-up; present bridge is only safe initial onboarding/local transport staging.

UI journey after prerequisites: Settings → Plugins → Add Spotify Connect → user selects engine and accepts its terms/auth steps → Connected players select **Garage Speakers** only → advertise player-name-only if desired → phone on same LAN opens Spotify device picker. Audio tests remain attended, with DSP mute/master safety boundary. External Spotify volume must never be treated as the DSP master ceiling.
