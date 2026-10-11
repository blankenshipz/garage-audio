"""Synthetic raw-event fixtures only, never live Spotify acceptance."""
import importlib.util
from pathlib import Path
import sys
import unittest

PATH=Path(__file__).resolve().parents[1]/'ma_provider/garage_speaker_control/connect_authority.py'
spec=importlib.util.spec_from_file_location('connect_authority_fixture',PATH)
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
Authority=module.ReceiverAuthority;Binding=module.ReceiverBinding;TARGET=module.TARGET

class Tests(unittest.TestCase):
    def setUp(self):
        self.now=1.;self.authority=Authority(clock=lambda:self.now)
        self.binding=Binding('spotify_connect_02_00_00_00_21_01',(123,456,'boot','sha'),1)
        self.authority.attach(self.binding,port_protected=True,daemon_alive=True)
        self.seq=0
        self.event('auth_state',logged_in=True,active=True)
    def event(self,kind,**values):
        self.seq+=1
        return self.authority.event(self.binding,self.seq,kind,**values)
    def token(self):
        self.event('playback_state',status='paused',active=True)
        return self.event('playback_changed',status='playing',active=True)
    def test_snapshots_and_repeated_playing_never_arm(self):
        self.assertIsNone(self.event('playback_state',status='playing',active=True))
        self.assertIsNone(self.event('playback_changed',status='playing',active=True))
        self.assertIsNone(self.event('playback_changed',status='playing',active=True))
    def test_one_use_target_and_graph_handoff(self):
        token=self.token();self.assertIsNotNone(token)
        for bad in (None,object(),module.Handoff(self.authority,self.binding,self.seq,self.authority.generation)):
            with self.assertRaises(PermissionError):self.authority.consume(bad,target=TARGET,graph_generation='zero-1')
        with self.assertRaises(PermissionError):self.authority.consume(token,target='other',graph_generation='zero-1')
        with self.assertRaises(PermissionError):self.authority.consume(token,target=TARGET,graph_generation=None)
        self.authority.consume(token,target=TARGET,graph_generation='zero-1')
        with self.assertRaises(PermissionError):self.authority.consume(token,target=TARGET,graph_generation='zero-1')
    def test_local_commands_deny_before_echo_can_arm(self):
        self.event('playback_state',status='paused',active=True)
        for command in ('play','resume','activate','next','previous','seek','add_to_queue','shuffle','repeat_context'):
            with self.assertRaises(PermissionError):self.authority.local_command(command)
        self.assertIsNone(self.event('playback_changed',status='playing',active=True))
    def test_only_exact_consumed_handoff_allows_queue_local_activation(self):
        token=self.token();self.authority.consume(token,target=TARGET,graph_generation='zero-1')
        self.authority.local_command('activate',handoff=token)
        self.authority.local_command('play',handoff=token)
        with self.assertRaises(PermissionError):self.authority.local_command('resume',handoff=object())
    def test_existing_scope_seek_track_controls_do_not_mint_or_reset_handoff(self):
        token=self.token();self.authority.consume(token,target=TARGET,graph_generation='zero-1')
        for command in ('seek','next','previous','add_to_queue'):
            self.authority.local_command(command)
            self.assertIs(self.authority._token,token)
        self.event('playback_changed',status='playing')
        self.assertIs(self.authority._token,token)
    def test_replay_revokes_and_old_epoch_cannot_arm(self):
        token=self.token()
        with self.assertRaises(PermissionError):self.authority.event(self.binding,self.seq,'playback_changed',status='playing')
        with self.assertRaises(PermissionError):self.authority.consume(token,target=TARGET,graph_generation='zero')
        new=Binding(self.binding.provider,self.binding.daemon,2)
        self.authority.attach(new,port_protected=True,daemon_alive=True)
        self.assertIsNone(self.authority.event(self.binding,99,'playback_changed',status='playing'))
    def test_pause_resume_and_stop_new_play(self):
        token=self.token();self.authority.consume(token,target=TARGET,graph_generation='zero-1')
        self.event('playback_changed',status='paused');self.assertEqual(self.authority.state,'paused')
        self.event('playback_state',status='playing');self.assertEqual(self.authority.state,'paused')
        self.assertIsNone(self.event('playback_changed',status='playing'));self.assertEqual(self.authority.state,'playing')
        self.event('playback_changed',status='stopped');self.assertEqual(self.authority.state,'stopped')
        new=self.event('playback_changed',status='playing');self.assertIsNotNone(new);self.assertIsNot(new,token)
    def test_timeout_disconnect_drift_and_pause_bound(self):
        token=self.token();self.authority.consume(token,target=TARGET,graph_generation='zero-1')
        self.now+=10
        with self.assertRaises(PermissionError):self.authority.renew(daemon_alive=True,ws_connected=True,source_current=True,graph_generation='zero-1')
        self.assertEqual(self.authority.state,'stopped')
    def test_wrong_binding_unprotected_endpoint_no_attach(self):
        for binding,protected,alive in ((Binding('x',(1,),1,'other'),True,True),(self.binding,False,True),(self.binding,True,False)):
            with self.assertRaises(PermissionError):Authority().attach(binding,port_protected=protected,daemon_alive=alive)
    def test_source_graph_or_receiver_loss(self):
        for change in ('daemon_alive','ws_connected','source_current','graph_generation'):
            self.setUp();token=self.token();self.authority.consume(token,target=TARGET,graph_generation='zero-1')
            arguments=dict(daemon_alive=True,ws_connected=True,source_current=True,graph_generation='zero-1')
            arguments[change]='changed' if change=='graph_generation' else False
            with self.assertRaises(PermissionError):self.authority.renew(**arguments)
    def test_pause_deadline_not_periodic_ui_deadline(self):
        token=self.token();self.authority.consume(token,target=TARGET,graph_generation='zero-1')
        self.event('playback_changed',status='paused')
        for _ in range(599):
            self.now+=1;self.authority.renew(daemon_alive=True,ws_connected=True,source_current=True,graph_generation='zero-1')
        self.now+=1
        with self.assertRaises(PermissionError):self.authority.renew(daemon_alive=True,ws_connected=True,source_current=True,graph_generation='zero-1')
    def test_inactive_then_fresh_selection_play_without_socket_restart(self):
        self.event('device_changed',active=False)
        self.event('device_changed',active=True)
        token=self.event('playback_changed',status='playing',active=True)
        self.assertIsNotNone(token)
    def pending_play(self):
        self.event('auth_state',logged_in=True,active=False)
        self.event('playback_state',status='paused',active=False)
        self.event('playback_changed',status='buffering')
        self.event('playback_state',status='buffering')
        self.assertIsNone(self.event('playback_changed',status='playing'))
        self.assertIsNone(self.authority._token)
        self.assertIsNotNone(self.authority._pending_remote_play)
    def test_fresh_play_then_late_device_selection_one_use(self):
        self.pending_play()
        self.event('playback_state',status='playing')
        self.now+=.5
        token=self.event('device_changed',active=True)
        self.assertIsNotNone(token)
        self.assertIsNone(self.authority._pending_remote_play)
        self.authority.consume(token,target=TARGET,graph_generation='zero-1')
        with self.assertRaises(PermissionError):self.authority.consume(token,target=TARGET,graph_generation='zero-1')
    def test_pending_play_cancellations_and_no_deadline_refresh(self):
        actions=[('playback_changed',{'status':s}) for s in ('paused','stopped','idle','buffering')]
        actions += [('playback_state',{'status':'paused'}),('playback_state',{'status':'playing','active':False}),('device_changed',{'active':False}),('auth_state',{'logged_in':False,'active':False})]
        for kind,fields in actions:
            with self.subTest(kind=kind,fields=fields):
                self.setUp();self.pending_play();self.event(kind,**fields)
                self.assertIsNone(self.event('device_changed',active=True))
                self.assertIsNone(self.authority._token)
        for command in ('pause','deactivate','activate'):
            self.setUp();self.pending_play()
            if command=='activate':
                with self.assertRaises(PermissionError):self.authority.local_command(command)
            else:self.authority.local_command(command)
            self.assertIsNone(self.event('device_changed',active=True))
        self.setUp();self.pending_play();deadline=self.authority._pending_remote_play[2]
        self.now+=1;self.event('playback_state',status='playing');self.event('playback_changed',status='playing')
        self.assertEqual(self.authority._pending_remote_play[2],deadline)
        self.now=deadline;self.assertIsNone(self.event('device_changed',active=True))
    def test_pending_replay_disconnect_epoch_and_explicit_false_rejected(self):
        for action in ('replay','revoke','end_window','attach'):
            self.setUp();self.pending_play()
            if action=='replay':
                with self.assertRaises(PermissionError):self.authority.event(self.binding,self.seq,'device_changed',active=True)
            elif action=='attach':self.authority.attach(Binding(self.binding.provider,self.binding.daemon,2),port_protected=True,daemon_alive=True)
            else:getattr(self.authority,action)()
            self.assertIsNone(self.authority._pending_remote_play)
            self.assertIsNone(self.event('device_changed',active=True))
        self.setUp();self.event('auth_state',logged_in=True,active=False)
        self.event('playback_changed',status='playing',active=False)
        self.assertIsNone(self.authority._pending_remote_play)
        self.assertIsNone(self.event('device_changed',active=True))
        self.setUp();self.event('auth_state',logged_in=True,active=False)
        self.event('playback_state',status='playing')
        self.assertIsNone(self.event('device_changed',active=True))
    def test_idle_closed_window_fresh_resume_can_prepare_again(self):
        token=self.token();self.authority.consume(token,target=TARGET,graph_generation='zero-1')
        self.event('playback_changed',status='paused');self.now+=600
        self.authority.end_window()
        fresh=self.event('playback_changed',status='playing',active=True)
        self.assertIsNotNone(fresh);self.assertIsNot(fresh,token)
    def test_supported_buffering_does_not_destroy_first_play_baseline(self):
        self.event('playback_state',status='stopped',active=True)
        self.assertIsNone(self.event('playback_changed',status='buffering',active=True))
        self.assertIsNotNone(self.event('playback_changed',status='playing',active=True))
    def test_cold_buffered_snapshot_cannot_arm(self):
        self.event('playback_state',status='buffering',active=True)
        self.assertIsNone(self.event('playback_changed',status='playing',active=True))
    def test_actual_receiver_buffering_delta_then_snapshot_keeps_play_baseline(self):
        self.event('playback_state',status='paused',active=True)
        self.event('device_changed',active=True)
        self.event('playback_changed',status='buffering',active=True)
        self.event('playback_state',status='buffering',active=True)
        token=self.event('playback_changed',status='playing',active=True)
        self.assertIsNotNone(token)
        self.authority.consume(token,target=TARGET,graph_generation='zero-1')
        self.assertIsNone(self.event('playback_state',status='playing',active=True))
        with self.assertRaises(PermissionError):self.authority.consume(token,target=TARGET,graph_generation='zero-1')
    def test_buffering_during_zero_prepare_keeps_original_one_use_token(self):
        token=self.token()
        self.event('playback_changed',status='buffering',active=True)
        self.assertIsNone(self.event('playback_changed',status='playing',active=True))
        self.assertIs(self.authority._token,token)
        self.authority.consume(token,target=TARGET,graph_generation='zero-1')
        with self.assertRaises(PermissionError):self.authority.consume(token,target=TARGET,graph_generation='zero-1')
    def test_buffering_owned_stream_mutes_and_idle_stops(self):
        token=self.token();self.authority.consume(token,target=TARGET,graph_generation='zero-1')
        self.event('playback_changed',status='buffering');self.assertEqual(self.authority.state,'buffering')
        self.assertIsNone(self.event('playback_changed',status='playing'));self.assertEqual(self.authority.state,'playing')
        self.event('playback_changed',status='idle');self.assertEqual(self.authority.state,'stopped');self.assertIsNone(self.authority._token)

if __name__=='__main__':unittest.main()
