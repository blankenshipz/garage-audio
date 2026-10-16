"""Optional private inherited-pipe scalar collector, not audio/consent authority.

Only a reviewed future transport plan invokes this wrapper. Current standby unchanged.
"""
import argparse
import json
import os
from pathlib import Path
import select
import signal
import stat
import struct
import subprocess
import threading

ROW = struct.Struct('<21q')
FIELDS = ('magic','process_epoch_ns','write_sequence','stream_epoch','before_submit_ns',
          'after_submit_ns','accepted_frame_base','first_nonzero_offset','requested',
          'accepted','gain_left','gain_right','base_left','base_right','rate','format',
          'channel_flags','mmap','silence','fault','frames_played')
LIMIT = 32768
MAX_ROWS = 64


def collect(fd, path, outcome):
    """Pipe bytes are fixed numeric scalars from a pinned writer, never PCM."""
    try:
        root=path.parent;info=root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:
            raise PermissionError('private owned scalar receipt directory required')
        target=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except Exception:
        outcome.update(fault=True,proof_complete=False);os.close(fd);raise
    pending=bytearray();size=0;count=0;last_seq=0;epoch=None;failed=False;nonzero=False;ended=False
    def record(value):
        nonlocal size
        encoded=(json.dumps(value,separators=(',',':'))+'\n').encode()
        if size+len(encoded)>LIMIT:raise ValueError('scalar receipt cap')
        os.write(target,encoded);size+=len(encoded)
    try:
        record({'schema':1,'fields':FIELDS,'gain_denominator':65536,
                'pcm_retained':False,'collector_time_is_not_application_time':True})
        while True:
            if not select.select([fd],[],[],.2)[0]:continue
            data=os.read(fd,4096)
            if not data:break
            pending.extend(data)
            while len(pending)>=ROW.size:
                values=ROW.unpack(pending[:ROW.size]);del pending[:ROW.size]
                if failed:continue  # drain only; no untrusted/raw data retention
                count+=1
                if count>MAX_ROWS or values[0]!=0x47415731 or values[2]<=last_seq or values[1]<=0:
                    failed=True;continue
                if epoch is None:epoch=values[1]
                if values[1]!=epoch:
                    failed=True;continue
                last_seq=values[2]
                if ended:failed=True
                elif values[19]==8:ended=True  # native normal-close scalar marker
                elif values[19]:failed=True
                elif not (0<=values[8]<=32768 and -4095<=values[9]<=values[8] and
                          0<values[4]<=values[5] and
                          (values[7]==-1 or 0<=values[7]<values[9]) and
                          all(-(1<<31)<=v<(1<<31) for v in values[10:14]) and
                          0<values[14]<=384000 and values[15]==0 and values[16]==0 and
                          values[17] in (0,1) and values[18] in (0,1)):
                    failed=True
                if not failed and values[19]==0 and values[7]>=0:nonzero=True
                record(list(values))
        if pending or not ended:failed=True
        outcome.update(rows=min(count,MAX_ROWS),fault=failed,bytes=size,
                       proof_complete=not failed and nonzero)
        record({'terminal':True,'rows':min(count,MAX_ROWS),'fault':failed,
                'pipe_eof':True,'native_terminal':ended,'proof_complete':outcome['proof_complete']})
    except Exception:
        outcome.update(fault=True,proof_complete=False)
        raise
    finally:
        pending.clear();os.close(target);os.close(fd)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args();command=args.command
    if command and command[0]=='--':command=command[1:]
    if not command:parser.error('reviewed pinned transport command required')
    read,write=os.pipe2(os.O_NONBLOCK|os.O_CLOEXEC)
    outcome={};errors=[]
    def reader():
        try:collect(read,args.receipt,outcome)
        except Exception as error:errors.append(type(error).__name__)
    thread=threading.Thread(target=reader);thread.start()
    child=None
    try:
        child=subprocess.Popen(command,env=dict(os.environ,GARAGE_SQUEEZ_GAIN_FD=str(write)),pass_fds=(write,))
        os.close(write);write=None
        def stop(signum,_):
            if child.poll() is None:child.send_signal(signum)
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        result=child.wait()
    finally:
        if write is not None:os.close(write)
        thread.join()
    # Telemetry failure is NOT a playback mutation/grant or a false positive proof.
    # Caller checks private receipt; default transport exit behavior stays the child's.
    if errors:print('gain witness unavailable: '+errors[0],file=__import__('sys').stderr)
    raise SystemExit(result)


if __name__=='__main__':
    main()
