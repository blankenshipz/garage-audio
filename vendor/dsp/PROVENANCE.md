# Pinned FILE-only job DSP dependency

CamillaDSP official v4.1.3 Linux amd64 release, version `4.1.3 (05e9cfc)`:
https://github.com/HEnquist/camilladsp/releases/download/v4.1.3/camilladsp-linux-amd64.tar.gz
Official GitHub release asset digest and local bytes match SHA256SUMS.
Executable SHA256: e69090287d9af87eeb5720d59cf8ebf8cab1ed3adbe24de1dd60b66a47f5385b.
Upstream tag resolves to commit 05e9cfcdf43c0dfe078ed3feb8af4c8bd701fd74:
https://github.com/HEnquist/camilladsp/tree/05e9cfcdf43c0dfe078ed3feb8af4c8bd701fd74
Corresponding upstream source archive retained unchanged:
https://codeload.github.com/HEnquist/camilladsp/tar.gz/05e9cfcdf43c0dfe078ed3feb8af4c8bd701fd74
Includes upstream build workflows and Cargo metadata. Cargo declares
`GPL-3.0-only OR MPL-2.0`; both upstream license texts retained in licenses/.
This is the official prebuilt binary, not a claim of independently reproducible
build or legal clearance. No upstream source or binary modifications.

Bookworm amd64 runtime library (NOT Ubuntu native host libraries):
https://deb.debian.org/debian/pool/main/a/alsa-lib/libasound2_1.2.8-1+b1_amd64.deb
Debian Bookworm Packages metadata digest and downloaded bytes match SHA256SUMS.
Library SHA256: 9dabddc79cc29b4f69058ac7d94ac11e82a4d6d8cc8c9148cb8377705790c7ea.
Package depends on libc>=2.34 and libasound2-data>=1.2.8-1. Only the library is
extracted for FILE processing; no ALSA device, configuration or plugin is used.
This is not a package-manager installation or a hardware backend.
Debian copyright notice and full upstream LGPL license retained in licenses/.
Corresponding source is alsa-lib1.2.8-1; +b1 denotes Debian binary rebuild.
Unchanged upstream source, Debian patches/build metadata and .dsc are retained:
https://deb.debian.org/debian/pool/main/a/alsa-lib/alsa-lib_1.2.8.orig.tar.bz2
https://deb.debian.org/debian/pool/main/a/alsa-lib/alsa-lib_1.2.8-1.debian.tar.xz
https://deb.debian.org/debian/pool/main/a/alsa-lib/alsa-lib_1.2.8-1.dsc
Digests from Debian Bookworm Sources metadata, checked before extraction.
No distribution/public publication or licensing-clearance claim is made here.

Build verifies archives and executable/library hashes. Fixed runtime executable
`/opt/garage-audio/dsp/camilladsp`, libraries `/opt/garage-audio/dsp/lib` are
root-owned and not writable by the application UID. Library lookup is scoped to
the owned Camilla child only; never set global LD_LIBRARY_PATH in the image.
Retained sources/licenses available in `/opt/garage-audio/dsp/vendor`.
No new port, native host service, package installation or audio device access.
