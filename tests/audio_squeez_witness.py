"""Synthetic numeric pipe collector checks, no audio or receiver connection."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from garage_audio.audio_squeez_witness import collect, ROW, LIMIT


class Collector(unittest.TestCase):
    def run_rows(self,rows,tail=b''):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'scalar.jsonl';read,write=os.pipe2(os.O_NONBLOCK)
            outcome={};errors=[]
            def reader():
                try:collect(read,path,outcome)
                except Exception as e:errors.append(type(e).__name__)
            thread=threading.Thread(target=reader);thread.start()
            for row in rows:
                data=memoryview(ROW.pack(*row))
                while data:
                    try:n=os.write(write,data);data=data[n:]
                    except BlockingIOError:__import__('time').sleep(.001)
            if tail:os.write(write,tail)
            os.close(write);thread.join(2);self.assertFalse(thread.is_alive());self.assertFalse(errors)
            data=path.read_bytes();self.assertLessEqual(len(data),LIMIT)
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            return outcome,[json.loads(line) for line in data.splitlines()]

    def row(self,seq=1):
        return [0x47415731,1,seq,1,100,101,0,3,16,16,32768,16384,32768,16384,48000,0,0,1,0,0,0]

    def end(self,seq):
        r=[0]*21;r[0]=0x47415731;r[1]=1;r[2]=seq;r[7]=-1;r[19]=8;return r

    def test_valid_scalar_first_nonzero_and_terminal(self):
        outcome,rows=self.run_rows([self.row(),self.end(2)])
        self.assertTrue(outcome['proof_complete']);self.assertEqual(rows[1][10:12],[32768,16384])
        self.assertTrue(rows[-1]['pipe_eof'])

    def test_error_prefix_and_loss_never_positive(self):
        r=self.row();r[9]=2;r[7]=-1
        outcome,_=self.run_rows([r]);self.assertFalse(outcome['proof_complete'])
        r=self.row();r[19]=1
        outcome,_=self.run_rows([r]);self.assertTrue(outcome['fault'])

    def test_partial_record_sequence_and_epoch_failure(self):
        outcome,_=self.run_rows([self.row()],b'x');self.assertTrue(outcome['fault'])
        outcome,_=self.run_rows([self.row(),self.row()]);self.assertTrue(outcome['fault'])
        r=self.row(2);r[1]=2
        outcome,_=self.run_rows([self.row(),r]);self.assertTrue(outcome['fault'])

    def test_64row_bound_worst_integer_lengths(self):
        rows=[]
        for seq in range(1,64):
            r=self.row(seq);r[1]=r[3]=r[6]=r[20]=(1<<63)-1
            r[4]=(1<<63)-2;r[5]=(1<<63)-1
            r[10:14]=[-(1<<31),(1<<31)-1,-(1<<31),(1<<31)-1]
            rows.append(r)
        end=self.end(64);end[1]=(1<<63)-1;rows.append(end)
        outcome,_=self.run_rows(rows);self.assertTrue(outcome['proof_complete'])
        rows.append(self.row(65))
        outcome,_=self.run_rows(rows);self.assertTrue(outcome['fault'])

    def test_eof_without_native_terminal_cannot_claim_complete(self):
        outcome,_=self.run_rows([self.row()])
        self.assertTrue(outcome['fault']);self.assertFalse(outcome['proof_complete'])


if __name__=='__main__':unittest.main()
