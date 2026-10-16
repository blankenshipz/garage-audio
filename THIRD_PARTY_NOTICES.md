# Third-party software

The root MIT license covers original Garage Audio code, not third-party software,
upstream-derived patches, recordings, or supplied calibration files.

* **CamillaDSP 4.1.3** declares GPL-3.0-only OR MPL-2.0. Unmodified binary,
  corresponding source, hashes and both license texts are retained in `vendor/dsp`.
  See [provenance](vendor/dsp/PROVENANCE.md) for exact upstream URLs.
* **ALSA lib 1.2.8 / Debian 1.2.8-1+b1**: library, corresponding source and Debian
  build metadata are retained in `vendor/dsp`; LGPL and Debian copyright notices
  are in `vendor/dsp/licenses`. These are not relicensed under MIT.
* **Music Assistant** images and dependencies retain upstream terms.
  `vendor/ma-volume/manifest.json` pins the base and patch hashes; the patch modifies
  upstream files without replacing their notices. Upstream:
  https://github.com/music-assistant/server (Apache-2.0).
* **Squeezelite** retains upstream GPL-3.0 terms. Optional gain-witness patch inputs
  and pinned provenance are under `vendor/squeezelite-gain`; upstream:
  https://github.com/ralph-irving/squeezelite. Any derivative binary release must
  retain corresponding source, build instructions and upstream license.
* Python, container and OS dependencies retain their own licenses. Requirements
  locks and image pins identify dependencies, not MIT permissions.

Do not remove retained vendor sources/licenses to reduce a release archive. The
Python wheel is not the complete Linux audio deployment and does not bundle DSP
binaries. No independently reproducible upstream binary build or blanket licensing
clearance is claimed.
