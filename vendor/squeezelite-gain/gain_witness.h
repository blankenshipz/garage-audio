/* GPL-3.0-or-later. Scalar-only ALSA witness; no gain/audio/authority mutation. */
#ifndef GARAGE_GAIN_WITNESS_H
#define GARAGE_GAIN_WITNESS_H
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>

/* Linux amd64 pinned build. Fixed little-endian int64 rows, <=PIPE_BUF. */
#if __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "gain witness requires pinned little-endian target"
#endif
enum { GW_MAGIC, GW_EPOCH, GW_SEQ, GW_STREAM, GW_BEFORE, GW_AFTER, GW_FRAME,
       GW_NONZERO, GW_REQUESTED, GW_ACCEPTED, GW_LEFT, GW_RIGHT, GW_BASE_LEFT,
       GW_BASE_RIGHT, GW_RATE, GW_FORMAT, GW_CHANNEL_FLAGS, GW_MMAP, GW_SILENCE,
       GW_FAULT, GW_PLAYED, GW_FIELDS };
enum { GW_LOST=1, GW_UNSUPPORTED=2, GW_OVERFLOW=4, GW_DONE=8 };
#define GW_TAG 0x47415731
#define GW_ROWS 64
#define GW_SCAN_FRAMES 32768
struct gw_row { int64_t v[GW_FIELDS]; };
_Static_assert(sizeof(struct gw_row) == 168, "fixed scalar ABI");
_Static_assert(sizeof(struct gw_row) <= 512, "atomic pipe record");
static struct {
    int fd, fault, count, written, first, nonzero, was_silent;
    uint64_t epoch, seq, stream, frames, played;
    int32_t left, right;
    struct gw_row pending[GW_ROWS], current;
} gw = { .fd=-1, .first=1, .was_silent=1 };

static uint64_t gw_ns(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) { gw.fault |= GW_LOST; return 0; }
    return (uint64_t)t.tv_sec*1000000000ULL + (uint64_t)t.tv_nsec;
}

/* Once, before output thread startup. Never opens a pathname or media file. */
static void gw_init(void) {
    const char *s=getenv("GARAGE_SQUEEZ_GAIN_FD"); char *end;
    struct stat st; long fd; int flags;
    if (!s || !*s) return;  /* stock default: disabled, no new output */
    errno=0; fd=strtol(s,&end,10);
    if (errno || *end || fd<3 || fd>INT_MAX) { gw.fault=GW_LOST; return; }
    flags=fcntl((int)fd,F_GETFL);
    if (flags<0 || !(flags&O_NONBLOCK) || (flags&O_ACCMODE)!=O_WRONLY ||
        fstat((int)fd,&st) || !S_ISFIFO(st.st_mode) || st.st_uid!=geteuid() ||
        (st.st_mode&0777)!=0600) { gw.fault=GW_LOST; return; }
    gw.fd=(int)fd; gw.epoch=gw_ns();
}

/* UNDER existing output mutex, AFTER transform, BEFORE submission. Bounded RAM only.
 * supported means PCM/S32LE/stereo, no crossfade; never export sample values. */
static void gw_before(const void *pcm, uint32_t frames, int silence,
                      int32_t left, int32_t right, int32_t base_left,
                      int32_t base_right, uint32_t rate, uint32_t format,
                      uint32_t flags, int mmap, uint64_t played, int supported) {
    struct gw_row *r=&gw.current;
    if (gw.fd<0 || gw.fault) return;
    memset(r,0,sizeof(*r));
    if ((!silence && gw.was_silent) || played<gw.played) {
        ++gw.stream; gw.nonzero=0; gw.first=1;
    }
    gw.was_silent=silence; gw.played=played;
    r->v[GW_MAGIC]=GW_TAG; r->v[GW_EPOCH]=(int64_t)gw.epoch;
    r->v[GW_SEQ]=(int64_t)++gw.seq; r->v[GW_STREAM]=(int64_t)gw.stream;
    r->v[GW_BEFORE]=(int64_t)gw_ns(); r->v[GW_FRAME]=(int64_t)gw.frames;
    r->v[GW_NONZERO]=-1; r->v[GW_REQUESTED]=frames;
    r->v[GW_LEFT]=left; r->v[GW_RIGHT]=right;
    r->v[GW_BASE_LEFT]=base_left; r->v[GW_BASE_RIGHT]=base_right;
    r->v[GW_RATE]=rate; r->v[GW_FORMAT]=format; r->v[GW_CHANNEL_FLAGS]=flags;
    r->v[GW_MMAP]=mmap; r->v[GW_SILENCE]=silence; r->v[GW_PLAYED]=(int64_t)played;
    if (!supported || frames>GW_SCAN_FRAMES) { r->v[GW_FAULT]=GW_UNSUPPORTED; return; }
    if (!silence && !gw.nonzero) {
        const unsigned char *p=pcm;
        for (uint32_t i=0;i<frames;++i) {
            uint32_t l,h; /* memcpy is unaligned-safe; values stay in RAM. */
            memcpy(&l,p+8*i,4); memcpy(&h,p+8*i+4,4);
            if (l || h) { r->v[GW_NONZERO]=i; break; }
        }
    }
}

/* UNDER existing mutex, immediately after submit returns (also error returns). */
static void gw_after(int64_t accepted) {
    struct gw_row *r=&gw.current;
    int select;
    if (gw.fd<0 || gw.fault) return;
    r->v[GW_AFTER]=(int64_t)gw_ns(); r->v[GW_ACCEPTED]=accepted;
    if (accepted>r->v[GW_REQUESTED]) { gw.fault|=GW_LOST; return; }
    if (accepted<=0 || r->v[GW_NONZERO]>=accepted) r->v[GW_NONZERO]=-1;
    select=gw.first || r->v[GW_LEFT]!=gw.left || r->v[GW_RIGHT]!=gw.right ||
           r->v[GW_NONZERO]>=0 || r->v[GW_FAULT] || accepted<0;
    if (select) {
        if (gw.written+gw.count>=GW_ROWS-1) { gw.fault|=GW_OVERFLOW; return; }
        gw.pending[gw.count++]=*r;
    }
    if (accepted>0) {
        gw.frames+=(uint64_t)accepted;
        gw.first=0;  /* do not flood the bounded witness during idle silence */
    }
    if (r->v[GW_NONZERO]>=0) gw.nonzero=1;
    gw.left=(int32_t)r->v[GW_LEFT]; gw.right=(int32_t)r->v[GW_RIGHT];
}

/* OUTSIDE the mutex. Thread-local SIGPIPE masking preserves upstream dispositions.
 * No disk IO/fsync, blocking writes, credential, sample or pathname fallback. */
static int gw_send(const struct gw_row *r) {
    sigset_t set, old, pending; int had, saved; ssize_t n;
    sigemptyset(&set); sigaddset(&set,SIGPIPE);
    if (pthread_sigmask(SIG_BLOCK,&set,&old)) return 0;
    sigpending(&pending); had=sigismember(&pending,SIGPIPE);
    n=write(gw.fd,r,sizeof(*r)); saved=errno;
    if (n<0 && saved==EPIPE && !had) {
        struct timespec zero={0,0};
        (void)sigtimedwait(&set,NULL,&zero);
    }
    (void)pthread_sigmask(SIG_SETMASK,&old,NULL);
    return n==(ssize_t)sizeof(*r);
}

static void gw_flush(void) {
    if (gw.fd<0 || gw.written>=GW_ROWS) return;
    for (int i=0;i<gw.count;++i) {
        if (!gw_send(&gw.pending[i])) { gw.fault|=GW_LOST; break; }
        ++gw.written;
    }
    gw.count=0;
    if (gw.fault) {
        struct gw_row lost={{0}};
        lost.v[GW_MAGIC]=GW_TAG; lost.v[GW_EPOCH]=(int64_t)gw.epoch;
        lost.v[GW_SEQ]=(int64_t)++gw.seq; lost.v[GW_NONZERO]=-1;
        lost.v[GW_FAULT]=gw.fault|GW_LOST;
        if (gw_send(&lost)) { ++gw.written; gw.fd=-1; }
    }
}
/* Only after output thread JOIN, at existing normal close. Missing terminal after
 * SIGKILL/EPIPE/full pipe must NEVER become a complete collection at pipe EOF. */
static void gw_finish(void) {
    gw_flush();
    if (gw.fd<0 || gw.fault || gw.written>=GW_ROWS) return;
    struct gw_row end={{0}};
    end.v[GW_MAGIC]=GW_TAG; end.v[GW_EPOCH]=(int64_t)gw.epoch;
    end.v[GW_SEQ]=(int64_t)++gw.seq; end.v[GW_NONZERO]=-1;
    end.v[GW_FAULT]=GW_DONE;
    if (gw_send(&end)) ++gw.written;
    else gw.fault|=GW_LOST;
}
#endif
