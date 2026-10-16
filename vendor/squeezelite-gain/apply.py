"""Apply the sole pinned ALSA writer derivative to a disposable source tree."""
import hashlib
from pathlib import Path
import sys

EXPECTED='0237c003c099f449a39032e6b0308322d7bb813b10247acc0eb0af9f7d708573'


def patched(original):
    assert hashlib.sha256(original).hexdigest()==EXPECTED,'wrong upstream output_alsa.c'
    v=original.decode()
    def replace(before,after):
        nonlocal v
        assert v.count(before)==1,before
        v=v.replace(before,after)
    replace('#if ALSA\n','#if ALSA\n\n#include "gain_witness.h"\n')
    replace('\tif (alsa.mmap) {\n\n\t\tsnd_pcm_sframes_t w = snd_pcm_mmap_commit',
        '\t/* Existing transform finished; scalar RAM witness precedes submission. */\n'
        '\tbool gw_pcm = true;\n'
        '#if DSD\n\tgw_pcm = output.outfmt == PCM;\n#endif\n'
        '\tgw_before(outputptr, out_frames, silence, gainL, gainR, output.gainL, output.gainR,\n'
        '\t          output.current_sample_rate, output.format, flags, alsa.mmap, output.frames_played,\n'
        '\t          gw_pcm && alsa.format == SND_PCM_FORMAT_S32_LE && flags == 0 &&\n'
        '\t          !(output.fade == FADE_ACTIVE && output.fade_dir == FADE_CROSS));\n'
        '\tif (alsa.mmap) {\n\n\t\tsnd_pcm_sframes_t w = snd_pcm_mmap_commit')
    replace('\t\tsnd_pcm_sframes_t w = snd_pcm_mmap_commit(pcmp, offset, out_frames);',
        '\t\tsnd_pcm_sframes_t w = snd_pcm_mmap_commit(pcmp, offset, out_frames);\n\t\tgw_after(w);')
    replace('\t\tsnd_pcm_sframes_t w = snd_pcm_writei(pcmp, outputptr, out_frames);',
        '\t\tsnd_pcm_sframes_t w = snd_pcm_writei(pcmp, outputptr, out_frames);\n\t\tgw_after(w);')
    replace('\t\tframes_t wrote = _output_frames(avail);\n\n\t\tUNLOCK;',
        '\t\tframes_t wrote = _output_frames(avail);\n\n\t\tUNLOCK;\n\t\tgw_flush();')
    replace('\tpthread_create(&thread, &attr, output_thread, rates[0] ? "probe" : NULL);',
        '\tgw_init();\n\tpthread_create(&thread, &attr, output_thread, rates[0] ? "probe" : NULL);')
    replace('\tpthread_join(thread, NULL);','\tpthread_join(thread, NULL);\n\tgw_finish();')
    return v.encode()


if __name__=='__main__':
    root=Path(sys.argv[1]);p=root/'output_alsa.c'
    p.write_bytes(patched(p.read_bytes()))
    (root/'gain_witness.h').write_bytes(Path(__file__).with_name('gain_witness.h').read_bytes())
