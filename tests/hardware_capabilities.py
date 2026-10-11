"""Isolated descriptor fixtures. No audio devices/streams or DSP service opened."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from garage_audio import hardware
from garage_audio.profile import DEFAULT, hardware_config

IDENTITY = dict(model='UMC1820',vendor='1397',product='0514',serial='FIXTURE_ONLY',alsa_card='UMC1820',pcm=0)


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.sound=self.root/'sound';self.sound.mkdir()
        self.usb=self.root/'usb';self.usb.mkdir()
        for name,value in [('idVendor','1397'),('idProduct','0514'),('serial','FIXTURE_ONLY')]:
            (self.usb/name).write_text(value)
        self.interface=self.usb/'interface';self.interface.mkdir()
        self.card=self.sound/'card7';self.card.mkdir()
        (self.card/'id').write_text('UMC1820')
        (self.card/'device').symlink_to(self.interface,target_is_directory=True)
        self.proc=self.root/'proc';(self.proc/'card7').mkdir(parents=True)
        self.sound_patch=patch.object(hardware,'SOUND_ROOT',self.sound)
        self.proc_patch=patch.object(hardware,'PROC_ROOT',self.proc)
        self.sound_patch.start();self.proc_patch.start()
        self.addCleanup(self.sound_patch.stop);self.addCleanup(self.proc_patch.stop)
        self.metadata()

    def metadata(self,channels=12,fmt='S24_3LE',rates='44100, 48000, 88200, 96000'):
        (self.proc/'card7'/'stream0').write_text(f'Fixture\nPlayback:\n  Status: Stop\n  Interface 1\n    Altset 1\n    Format: {fmt}\n    Channels: {channels}\n    Rates: {rates}\nCapture:\n  Status: Stop\n')

    def test_detect_compile_packed24(self):
        cap=hardware.playback_capabilities(IDENTITY)
        hardware.admit_capabilities(IDENTITY,cap)
        config=hardware_config(DEFAULT,'hw:CARD=UMC1820,DEV=0',cap)
        self.assertEqual(config['devices']['playback'],dict(type='Alsa',channels=12,device='hw:CARD=UMC1820,DEV=0',format='S24_3_LE'))
        self.assertEqual(config['devices']['samplerate'],48000)
        self.assertEqual(config['mixers']['route']['channels'],{'in':2,'out':12})
        self.assertEqual([m['dest'] for m in config['mixers']['route']['mapping']],[2,3,4])
        self.assertEqual(set(range(12))-{2,3,4},{0,1,5,6,7,8,9,10,11})
        self.assertEqual(config['filters']['headroom']['parameters']['gain'],-6)
        self.assertEqual(config['filters']['hp']['parameters']['freq'],80)

    def test_legacy20_s32_unchanged_and_admitted_only_if_matching(self):
        legacy=hardware.load_pinned_capabilities(self.root/'absent',IDENTITY)
        config=hardware_config(DEFAULT,'hw:CARD=UMC1820,DEV=0')
        self.assertEqual(config['devices']['playback']['channels'],20)
        self.assertEqual(config['devices']['playback']['format'],'S32_LE')
        with self.assertRaises(ValueError):hardware.admit_capabilities(IDENTITY,legacy)
        self.metadata(20,'S32_LE')
        self.assertEqual(hardware.admit_capabilities(IDENTITY,legacy),legacy)
        self.assertEqual(hardware_config(DEFAULT,'hw:CARD=UMC1820,DEV=0',legacy),config)

    def test_optical_switch_change_rejected_not_recompiled(self):
        cap=hardware.playback_capabilities(IDENTITY)
        self.metadata(20,'S24_3LE')
        with self.assertRaises(ValueError):hardware.admit_capabilities(IDENTITY,cap)

    def test_serial_mismatch_rejected(self):
        (self.usb/'serial').write_text('DIFFERENT')
        with self.assertRaises(ValueError):hardware.playback_capabilities(IDENTITY)

    def test_disconnected_rejected(self):
        (self.card/'id').unlink()
        with self.assertRaises(ValueError):hardware.playback_capabilities(IDENTITY)

    def test_ambiguous_card_rejected(self):
        other=self.sound/'card8';other.mkdir();(other/'id').write_text('UMC1820')
        with self.assertRaises(ValueError):hardware.playback_capabilities(IDENTITY)

    def test_unknown_layout_format_rate_rejected(self):
        for channels,fmt,rates in [(2,'S24_3LE','48000'),(12,'FLOAT_LE','48000'),(12,'S24_3LE','96000')]:
            with self.subTest(channels=channels,fmt=fmt,rates=rates):
                self.metadata(channels,fmt,rates)
                with self.assertRaises(ValueError):hardware.playback_capabilities(IDENTITY)

    def test_multiple_alternate_settings_rejected(self):
        path=self.proc/'card7'/'stream0'
        path.write_text(path.read_text().replace('Capture:','    Altset 2\n    Format: S32_LE\n    Channels: 12\n    Rates: 48000\nCapture:'))
        with self.assertRaises(ValueError):hardware.playback_capabilities(IDENTITY)

    def test_schema_identity_and_compiler_device_mismatch_rejected(self):
        cap=hardware.playback_capabilities(IDENTITY)
        for key,value in [('channels',True),('format',[]),('samplerate',96000),('extra',1)]:
            invalid=copy.deepcopy(cap);invalid[key]=value
            with self.assertRaises(ValueError):hardware.validate_capabilities(invalid)
        other=dict(IDENTITY,serial='OTHER')
        with self.assertRaises(ValueError):hardware.validate_capabilities(cap,other)
        invalid=copy.deepcopy(cap);invalid['identity']['pcm']=1
        with self.assertRaises(ValueError):hardware.validate_capabilities(invalid)
        with self.assertRaises(ValueError):hardware_config(DEFAULT,'hw:CARD=PCH,DEV=0',cap)

    def test_mutable_or_symlink_policy_rejected(self):
        path=self.root/'capabilities.json'
        path.write_text(json.dumps(hardware.playback_capabilities(IDENTITY)))
        with self.assertRaises(PermissionError):hardware.load_pinned_capabilities(path,IDENTITY)
        link=self.root/'link';link.symlink_to(path)
        with self.assertRaises(PermissionError):hardware.load_pinned_capabilities(link,IDENTITY)

    def test_physical_receipt_never_reused_across_capability_change(self):
        state=self.root/'state';state.mkdir()
        cap=hardware.playback_capabilities(IDENTITY)
        hardware.admit_map_capabilities(state,cap)
        (state/'physical-map-verified').write_text('FIXTURE_ONLY')
        with self.assertRaises(ValueError):hardware.admit_map_capabilities(state,cap)
        (state/'playback-capabilities.json').write_text(json.dumps(cap))
        hardware.admit_map_capabilities(state,cap)
        changed=dict(cap,channels=20)
        with self.assertRaises(ValueError):hardware.admit_map_capabilities(state,changed)


if __name__=='__main__':unittest.main()
