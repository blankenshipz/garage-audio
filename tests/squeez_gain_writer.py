"""Compile REAL pinned writer/pack functions; synthetic ALSA boundary, no DAC/media.

Run with cached gcc and public source path. This is NOT a full production binary build.
"""
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys

root=Path(sys.argv[1]);up=Path(sys.argv[2]);out=Path(sys.argv[3])
out.mkdir(parents=True,exist_ok=True)
vendor=root/'vendor/squeezelite-gain'
manifest=json.loads((vendor/'upstream.json').read_text())
for name,digest in manifest['files'].items():
    assert hashlib.sha256((up/name).read_bytes()).hexdigest()==digest,name
spec=importlib.util.spec_from_file_location('witness_patch',vendor/'apply.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
original=(up/'output_alsa.c').read_bytes();patched=module.patched(original)
(out/'output_alsa.c').write_bytes(patched)
(out/'gain_witness.h').write_bytes((vendor/'gain_witness.h').read_bytes())

def function(text,start):
    # Preserve offsets while ignoring braces in C comments/string literals.
    mask=re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"',lambda m:' '*len(m[0]),text,flags=re.S)
    at=text.index(start);brace=mask.index('{',at);depth=1;i=brace+1
    while depth:
        depth+=(mask[i]=='{')-(mask[i]=='}');i+=1
    return text[at:i]

pack=(up/'output_pack.c').read_text()
functions='\n'.join(function(pack,name) for name in ('s32_t gain(', 'void _scale_and_pack_frames(', 'void _apply_gain('))
writer=function(patched.decode(),'static int _write_frames(')
control=function(original.decode(),'static int _write_frames(').replace('_write_frames(', '_write_frames_control(',1)
setter=function(original.decode(),'void set_volume(')
prefix=r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <pthread.h>
#include <stdatomic.h>
#include <math.h>
typedef int32_t s32_t; typedef int64_t s64_t; typedef uint8_t u8_t;
typedef uint32_t u32_t; typedef uint32_t frames_t;
typedef enum { S32_LE, S24_LE, S24_3LE, S16_LE } output_format;
#define FIXED_ONE 65536
#define MAX_SCALESAMPLE 0x7fffffffffffLL
#define MIN_SCALESAMPLE -MAX_SCALESAMPLE
#define MONO_LEFT 1
#define MONO_RIGHT 2
#define BYTES_PER_FRAME 8
#define ISAMPLE_T s32_t
#define SL_LITTLE_ENDIAN 1
#define IF_DSD(x)
#define DSD 0
#define FADE_ACTIVE 1
#define FADE_CROSS 2
#define SND_PCM_FORMAT_S32_LE 10
#define NATIVE_FORMAT SND_PCM_FORMAT_S32_LE
#define LOG_WARN(...) ((void)0)
#define LOG_DEBUG(...) ((void)0)
#define LOCK pthread_mutex_lock(&audio_mutex)
#define UNLOCK pthread_mutex_unlock(&audio_mutex)
static int locked, sent_rows;
static ssize_t fixture_write(int fd,const void *p,size_t n) {
    assert(!locked); /* emitter must be AFTER the existing output mutex unlock */
    ++sent_rows; return syscall(SYS_write,fd,p,n);
}
#define write fixture_write
#include "gain_witness.h"
#undef write
struct buffer { u8_t *readp; } ob; static struct buffer *outputbuf=&ob;
static struct { int fade,fade_dir; unsigned gainL,gainR,current_sample_rate;
    output_format format; uint64_t frames_played; } output;
static struct { bool mmap; int format; u8_t *write_buf; const char *volume_mixer_name; } alsa;
typedef long snd_pcm_sframes_t; typedef unsigned long snd_pcm_uframes_t;
typedef struct { void *addr; unsigned first,step; } snd_pcm_channel_area_t;
static void *pcmp=(void*)1;
static s32_t input[64], destination[64], submitted[64];
static u8_t silence_storage[256], *silencebuf=silence_storage;
static snd_pcm_channel_area_t area;
static int64_t mock_return; static unsigned submitted_frames;
static pthread_mutex_t audio_mutex=PTHREAD_MUTEX_INITIALIZER;
static atomic_int submit_gate, submit_entered, submit_release, setter_done;
static void set_mixer(bool silence,float left,float right) { (void)silence;(void)left;(void)right; }
static int snd_pcm_avail_update(void *p) { (void)p;return 32; }
static int snd_pcm_mmap_begin(void *p,const snd_pcm_channel_area_t **a,
    snd_pcm_uframes_t *offset,snd_pcm_uframes_t *frames) {
    (void)p;(void)frames;area.addr=destination;area.first=0;area.step=64;
    *a=&area;*offset=0;return 0;
}
static void before_submit(const void *ptr,unsigned frames) {
    assert(locked); assert(gw.fd<0 || gw.current.v[GW_BEFORE]>0);
    if (submit_gate) { submit_entered=1;while(!submit_release)usleep(100); }
    submitted_frames=mock_return>0?(unsigned)mock_return:0;
    assert(submitted_frames<=frames);
    memcpy(submitted,ptr,submitted_frames*8); /* synthetic fixture RAM only */
}
static snd_pcm_sframes_t snd_pcm_mmap_commit(void *p,snd_pcm_uframes_t off,
    snd_pcm_uframes_t frames) { (void)p;(void)off;before_submit(destination,frames);return mock_return; }
static snd_pcm_sframes_t snd_pcm_writei(void *p,void *ptr,snd_pcm_uframes_t frames) {
    (void)p;before_submit(ptr,frames);return mock_return;
}
static int snd_pcm_recover(void *p,long error,int silent) { (void)p;(void)error;(void)silent;return 0; }
static void alsa_close(void) {}
static const char *snd_strerror(long error) { (void)error;return "synthetic"; }
static void _apply_cross(struct buffer *b,unsigned n,int l,int r,s32_t **p) {
    (void)b;(void)n;(void)l;(void)r;(void)p;assert(0); }
'''
tests=r'''
static int pipes[2];
static void setup(int mmap) {
    memset(&gw,0,sizeof(gw));gw.fd=-1;gw.first=1;gw.was_silent=1;
    assert(!pipe2(pipes,O_NONBLOCK|O_CLOEXEC));char fd[32];sprintf(fd,"%d",pipes[1]);
    setenv("GARAGE_SQUEEZ_GAIN_FD",fd,1);gw_init();assert(gw.fd==pipes[1]);
    outputbuf->readp=(u8_t*)input;alsa.mmap=mmap;alsa.format=NATIVE_FORMAT;
    alsa.write_buf=(u8_t*)destination;output.format=S32_LE;output.current_sample_rate=48000;
    output.gainL=output.gainR=32768;output.frames_played=0;mock_return=16;
    memset(input,0,sizeof(input));for(int i=12;i<16;i++)input[2*i]=1048576;
    sent_rows=0;memset(submitted,0,sizeof(submitted));
}
static void done(void) {close(pipes[0]);close(pipes[1]);}
static int call(int32_t left,int32_t right,int silence) {
    s32_t *cross=NULL;pthread_mutex_lock(&audio_mutex);locked=1;
    int result=_write_frames(16,silence,left,right,0,0,0,&cross);
    locked=0;pthread_mutex_unlock(&audio_mutex);gw_flush();return result;
}
static struct gw_row row(void) {struct gw_row r;assert(read(pipes[0],&r,sizeof(r))==sizeof(r));return r;}
static void test_paths_bytes_and_order(int mmap) {
    setup(mmap);for(int i=12;i<16;i++)input[2*i+1]=-2097152;
    assert(call(32768,16384,0)==16);struct gw_row r=row();
    assert(r.v[GW_LEFT]==32768 && r.v[GW_RIGHT]==16384 && r.v[GW_NONZERO]==12);
    assert(r.v[GW_ACCEPTED]==16 && r.v[GW_BEFORE]<=r.v[GW_AFTER]);
    assert(r.v[GW_FAULT]==0 && sent_rows==1 && submitted[24]==524288 && submitted[25]==-524288);
    s32_t expected[64];memcpy(expected,submitted,sizeof(expected));
    memset(input,0,sizeof(input));for(int i=12;i<16;i++){input[2*i]=1048576;input[2*i+1]=-2097152;}
    s32_t *cross=NULL;int saved=gw.fd;gw.fd=-1;locked=1;
    assert(_write_frames_control(16,0,32768,16384,0,0,0,&cross)==16);
    locked=0;gw.fd=saved;assert(!memcmp(expected,submitted,16*8));done();
}
static void test_default_change_partial_error(int mmap) {
    setup(mmap);output.gainL=output.gainR=0;assert(call(0,0,0)==16);
    struct gw_row r=row();assert(r.v[GW_LEFT]==0 && r.v[GW_NONZERO]==-1);
    memset(input,0,sizeof(input));for(int i=12;i<16;i++)input[2*i]=1048576;
    output.gainL=output.gainR=32768;mock_return=8;
    assert(call(32768,32768,0)==(mmap?-1:8));r=row();
    assert(r.v[GW_ACCEPTED]==8 && r.v[GW_NONZERO]==-1); /* nonzero outside accepted prefix */
    memset(input,0,sizeof(input));input[0]=1048576;mock_return=16;
    assert(call(32768,32768,0)==16);r=row();assert(r.v[GW_NONZERO]==0);
    mock_return=-EAGAIN;assert(call(32768,32768,0)==-1);r=row();
    assert(r.v[GW_ACCEPTED]==-EAGAIN && r.v[GW_NONZERO]==-1);done();
}
static void test_pipe_and_cap(void) {
    setup(0);char fill[4096]={0};while(syscall(SYS_write,pipes[1],fill,sizeof(fill))>0){}
    uint64_t before=gw_ns();assert(call(32768,32768,0)==16);
    assert(gw_ns()-before<100000000ULL && (gw.fault&GW_LOST));
    while(read(pipes[0],fill,sizeof(fill))>0){} gw_flush();struct gw_row r=row();
    assert(r.v[GW_FAULT]&GW_LOST);done();
    setup(0);close(pipes[0]);assert(call(32768,32768,0)==16);assert(gw.fault&GW_LOST);
    close(pipes[1]); /* EPIPE did not terminate the audio process via SIGPIPE */
    setup(0);
    for(int i=0;i<80;i++) {
        memset(input,0,sizeof(input));input[0]=1048576;
        output.gainL=output.gainR=10000+i;
        call(10000+i,10000+i,0);
        while(read(pipes[0],fill,sizeof(fill))>0){}
    }
    assert(gw.written==64 && (gw.fault&GW_OVERFLOW));done();
    setup(0);mock_return=16;
    for(int i=0;i<100;i++)call(FIXED_ONE,FIXED_ONE,1);
    assert(gw.written==1 && !gw.fault);done(); /* idle does not exhaust capture */
}
static void test_bad_descriptor_and_unsupported(void) {
    setup(0);close(pipes[0]);close(pipes[1]);memset(&gw,0,sizeof(gw));gw.fd=-1;
    int file=open("/tmp/not-a-pipe",O_WRONLY|O_CREAT|O_TRUNC|O_NONBLOCK,0600);
    char text[32];sprintf(text,"%d",file);setenv("GARAGE_SQUEEZ_GAIN_FD",text,1);gw_init();
    assert(gw.fd==-1 && gw.fault);close(file);
    setup(0);output.fade=FADE_ACTIVE;output.fade_dir=FADE_CROSS;
    gw_before(input,16,0,1,1,1,1,48000,0,0,0,0,0);gw_after(16);gw_flush();
    struct gw_row r=row();assert(r.v[GW_FAULT]&GW_UNSUPPORTED);done();
}
static void *writer_actor(void *p) { (void)p;assert(call(32768,32768,0)==16);return NULL; }
static void *setter_actor(void *p) { (void)p;set_volume(16384,16384);setter_done=1;return NULL; }
static void test_setter_write_mutex_order(void) {
    setup(0);submit_gate=1;submit_entered=submit_release=setter_done=0;
    pthread_t writer_thread,setter_thread;
    assert(!pthread_create(&writer_thread,NULL,writer_actor,NULL));
    while(!submit_entered)usleep(100);
    assert(!pthread_create(&setter_thread,NULL,setter_actor,NULL));usleep(10000);
    assert(!setter_done && output.gainL==32768);
    submit_release=1;pthread_join(writer_thread,NULL);pthread_join(setter_thread,NULL);
    struct gw_row r=row();assert(r.v[GW_BASE_LEFT]==32768 && r.v[GW_LEFT]==32768);
    assert(output.gainL==16384 && setter_done);submit_gate=0;done();
}
int main(int argc,char **argv) {
    if (argc==2 && !strcmp(argv[1],"--inherited")) {
        const char *value=getenv("GARAGE_SQUEEZ_GAIN_FD");assert(value);
        char inherited[32];snprintf(inherited,sizeof(inherited),"%s",value);
        setup(0);close(pipes[0]);close(pipes[1]);
        memset(&gw,0,sizeof(gw));gw.fd=-1;gw.first=1;gw.was_silent=1;
        setenv("GARAGE_SQUEEZ_GAIN_FD",inherited,1);gw_init();assert(gw.fd>=0);
        assert(call(32768,32768,0)==16);gw_finish();return 0;
    }
    test_paths_bytes_and_order(0);test_paths_bytes_and_order(1);
    test_default_change_partial_error(0);test_default_change_partial_error(1);
    test_pipe_and_cap();test_bad_descriptor_and_unsupported();
    output.fade=0;test_setter_write_mutex_order();
    puts("PASS actual pinned writer/packing: mmap/writei, factors/default, prefix/error, bytes, unlock-order, full/broken pipe, idle/cap/descriptor");
}
'''
fixture=out/'writer_fixture.c';fixture.write_text(prefix+'\n'+functions+'\n'+setter+'\n'+writer+'\n'+control+'\n'+tests)
subprocess.run(['gcc','-std=gnu11','-O2','-Wall','-Wextra','-Werror','-Wno-unused-function',
                '-pthread','-I',str(out),str(fixture),'-lm','-o',str(out/'writer_fixture')],check=True)
if '--wrapper-only' not in sys.argv[4:]:
    subprocess.run([str(out/'writer_fixture')],check=True)
private=out/'private';private.mkdir(mode=0o700)
os.chown(private,1002,1003)
def worker_uid():
    os.setgid(1003);os.setuid(1002)
subprocess.run([sys.executable,'-m','garage_audio.audio_squeez_witness',
                '--receipt',str(private/'scalars.jsonl'),'--',str(out/'writer_fixture'),'--inherited'],
               check=True,preexec_fn=worker_uid)
rows=[json.loads(line) for line in (private/'scalars.jsonl').read_text().splitlines()]
assert rows[-1]['proof_complete'] and rows[-1]['rows']==2 and not rows[-1]['fault']
assert rows[1][7]==12 and rows[1][10:12]==[32768,32768]
print('PASS UID1002:GID1003 wrapper inherited NONBLOCK descriptor -> actual writer -> bounded private scalar receipt')
print(json.dumps({'upstream':manifest['commit'],'actual_writer_function':True,
                  'alsa_boundary':'synthetic in-memory stubs','production_binary_built':False,
                  'patched_alsa_sha256':hashlib.sha256(patched).hexdigest(),
                  'fixture_source_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),
                  'fixture_binary_sha256':hashlib.sha256((out/'writer_fixture').read_bytes()).hexdigest()}))
