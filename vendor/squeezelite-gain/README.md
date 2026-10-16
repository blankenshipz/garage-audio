# Pinned scalar applied-gain witness (not yet deployed)

`upstream.json` pins Squeezelite commit/package and original files/binary. `apply.py`
accepts ONLY the exact pinned `output_alsa.c` and modifies only its writer, existing
post-unlock output-thread path, and startup/normal-close calls. `gain_witness.h` adds
bounded process-local numeric telemetry, not gain logic or playback authority.

- AFTER existing scaling, BEFORE mmap commit/writei: copy actual effective/base 16.16
  gains, local epochs, frame indices and timestamps; scan only existing S32LE stereo
  destination RAM for a first-nonzero INTEGER index. No samples leave RAM.
- AFTER submission: constrain first-nonzero to the accepted prefix, retain error/partial
  facts. AFTER unlock: one atomic NONBLOCK inherited private pipe write per selected
  row. EAGAIN/EPIPE/overflow are sticky proof loss, never sound grants or file fallback.
- Fixed21-int64 little-endian rows168B, max64 INCLUDING a reserved terminal/fault row;
  bounded32768-frame scan; unsupported format/mono/crossfade fails qualification.
- At existing normal close AFTER output-thread join: native DONE marker. Pipe EOF alone
  cannot establish complete collection (SIGKILL/drop may hide the last record).
- Absent witness FD keeps stock tracing disabled; neither audio bytes, gains, defaults,
  public protocols nor essential receiver/source/ticket/freshness policy changes.

The optional operator wrapper is `garage_audio.audio_squeez_witness`: it creates the
anonymous NONBLOCK inherited pipe, launches the pinned process with that descriptor,
and writes at most32KiB numeric JSON into a private0700-owned directory/new0600 file.
It forwards termination and preserves child exit behavior. No public/MCP command or
credential/approval interface exists. No production transport/Docker entrypoint is
rewired in this change; reviewed packaging must explicitly wire the optional wrapper.
The collector's complete evidence requires native DONE + EOF + accepted-first-nonzero
and no faults; do not kill/restart restored standby to manufacture this condition.
Live first-nonzero rows and a complete terminal collection are distinct evidence.

Focused tests compile the REAL pinned `_write_frames`, `set_volume`, `gain`, packing
and apply functions with a SYNTHETIC in-memory ALSA boundary. Both mmap/writei, numeric
factors/default0, accepted prefixes/errors, unchanged bytes, real mutex-setter ordering,
full/broken pipe, idle/cap/descriptor denial and UID1002:GID1003 inherited-wrapper path
are tested. Collector tests cover row/cap/missing terminal/sequence/epoch failures.
These are not a full Squeezelite binary build, real libasound or live Spotify proof.

Cached `python:3.11.14-bookworm` has GCC12.2 but lacks
`/usr/include/alsa/asoundlib.h`. Running audio83fb also lacks that header/compiler.
Thus the complete production derivative is NOT built: a verified already-cached ALSA
development-header/build context is the exact first prerequisite. No dependencies
installed/downloaded, image pull, live source overlay or binary replacement performed.
Package owner must also preserve upstream feature flags/dependency fingerprint and
pin the NEW derivative binary hash; never label the synthetic fixture as that binary.

Current deployed standby83fb/consumedr15/discovery stays unchanged. No fresh budget,
Play/Pause/seek/volume/unmute/inference/hardware/approval follows from these tests.
