"""Project MA-owned attended check; auth revalidation stays inside MA."""
import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from music_assistant.models.plugin import PluginProvider
from music_assistant.controllers.webserver.helpers.auth_middleware import (
    current_user, get_current_token, get_impersonated_user,
    has_scope,
)
from music_assistant_models.auth import Scope
from music_assistant_models.config_entries import ConfigEntry, ConfigActionResult
from music_assistant_models.enums import ConfigEntryType
from music_assistant_models.errors import InsufficientPermissions, ActionUnavailable
from .client import Connection


@dataclass(frozen=True)
class SessionIdentity:
    """In-process non-bearer identity; never returned to browser/logged."""
    user_id: str
    session_digest: str
    expires_at: datetime


async def interactive_identity(mass):
    """Revalidate real dispatch context, not request-body actor/role strings."""
    principal=current_user.get()
    token=get_current_token()
    if get_impersonated_user() is not None or principal is None or not token:
        raise InsufficientPermissions('A non-impersonated Music Assistant login is required')
    if principal.role not in ('admin','user') or not principal.enabled:
        raise InsufficientPermissions('An active interactive account is required')
    auth=mass.webserver.auth
    validated=await auth.authenticate_with_token(token)
    if validated is None or validated.user_id!=principal.user_id or validated.role not in ('admin','user') or not validated.enabled:
        raise InsufficientPermissions('Music Assistant login was revoked or changed')
    if not has_scope(validated,Scope.CONFIG_PROVIDERS_WRITE):
        raise InsufficientPermissions('Speaker-control action permission revoked')
    token_id=await auth.get_token_id_from_token(token)
    if not token_id:raise InsufficientPermissions('Validated session metadata required')
    row=await auth.database.get_row('auth_tokens',{'token_id':token_id})
    if not row or row['user_id']!=validated.user_id or row['is_long_lived']!=0 or not row['expires_at']:
        raise InsufficientPermissions('Integration tokens cannot approve speaker output')
    expiry=datetime.fromisoformat(row['expires_at'])
    if expiry.tzinfo is None or expiry<=datetime.now(timezone.utc):
        raise InsufficientPermissions('Current session expiry required')
    return SessionIdentity(validated.user_id,hashlib.sha256(token_id.encode()).hexdigest(),expiry)


async def setup(mass,manifest,config):
    return GarageSpeakerControl(mass,manifest,config,set())


class GarageSpeakerControl(PluginProvider):
    async def handle_async_init(self):
        self._windows={};self._monitors=set();self._proposals={}
        from .connect_runtime import ConnectRuntime
        self._connect_runtime=ConnectRuntime(self.mass);self._connect_runtime.install()
    def player_readback(self):
        state=self.mass.players.get_player_state('02:00:00:00:21:01')
        if not state or not state.available or state.name!='Garage Speakers':raise ActionUnavailable('Garage Speakers is not connected')
        value=state.playback_state.value
        if value not in ('playing','paused','idle'):raise ActionUnavailable('Garage Speakers transport is unknown')
        return {'state':'off' if state.powered is False else value,'volume':state.volume_level}
    async def unload(self,is_removed=False):
        if getattr(self,'_connect_runtime',None):await self._connect_runtime.close()
        for connection in list(self._windows.values()):await connection.close()
        for task in self._monitors:task.cancel()
        if self._monitors:await asyncio.gather(*self._monitors,return_exceptions=True)
    async def watch(self,connection):
        # Task inherits the actual authenticated MA dispatch ContextVars. A phone
        # backgrounding does not stop this server task or extend native deadline.
        try:
            while not connection.closed:
                identity=await interactive_identity(self.mass)
                if identity.session_digest!=connection.session:raise InsufficientPermissions('Session changed')
                if getattr(connection,'normal',False):
                    player=self.player_readback()
                    if player['state']=='playing':connection.seen_play=True
                    elif player['state']=='off' and not getattr(connection,'seen_play',False):player['state']='idle'
                    status=await connection.request('normal-heartbeat',**player)
                else:status=await connection.request('heartbeat')
                if status['state'] in ('stopped','stop_unverified'):break
                await asyncio.sleep(1)
        except (Exception,asyncio.CancelledError):pass
        finally:
            await connection.close()
            if self._windows.get(connection.session) is connection:self._windows.pop(connection.session,None)
    async def get_config_entries(self):
        connection=None;identity=None
        if getattr(self,'_windows',None):
            try:identity=await interactive_identity(self.mass);connection=self._windows.get(identity.session_digest)
            except InsufficientPermissions:pass
        state=connection.state.get('state') if connection else 'stopped'
        runtime=getattr(self,'_connect_runtime',None);connect_state=runtime.status() if runtime else 'disabled'
        entries=[
            ConfigEntry(key='spotify_connect_status',type=ConfigEntryType.LABEL,
                         label='Spotify Connect: '+connect_state+' · Everyday playback uses Spotify → Garage Speakers → Play, not the manual session controls below. If disabled, automatic sound is not armed yet. Keep Fosi fully down for first playback, then raise slowly. Spotify volume is digital attenuation; Fosi is the main volume control.'),
            ConfigEntry(key='manual_fallback',type=ConfigEntryType.LABEL,label='Optional manual fallback / other sources — separate from Spotify Connect'),
            ConfigEntry(key='listening_boundary',type=ConfigEntryType.LABEL,
                         label='Garage Speakers · Fosi knob is the main volume control. Fixed stereo line output: unity DSP master and one −6 dB processing-headroom stage, no crossover. MA volume is optional digital attenuation. This is substantially louder than the old −20/−30 modes. Turn Fosi fully down before Start, then raise it slowly after you press Play. Digital headroom is not an SPL safety guarantee.'),
            ConfigEntry(key='verify_session',type=ConfigEntryType.ACTION,
                        label='Check login (silent)',action='verify_session',required=False),
            ConfigEntry(key='stop_output',type=ConfigEntryType.ACTION,label='Stop speaker output now',action='stop_output',required=False),
            ConfigEntry(key='output_state',type=ConfigEntryType.LABEL,label='Speaker output: '+state),
        ]
        if state=='stopped':
            try:identity=identity or await interactive_identity(self.mass)
            except InsufficientPermissions:identity=None
            hours=getattr(self,'_proposals',{}).get(identity.session_digest,2) if identity else 2
            for h in (1,2,4):entries.append(ConfigEntry(key=f'duration_{h}',type=ConfigEntryType.ACTION,label=f'{h} hour'+('s' if h!=1 else '')+(' · selected' if hours==h else ''),action=f'duration_{h}',required=False))
            entries.append(ConfigEntry(key='consent_scope',type=ConfigEntryType.LABEL,label=f'By pressing Start listening, I confirm Fosi is fully down, I am beside it, and approve {hours} hours of the NEW fixed stereo line output on OUT3/4 only (unity master, −6 dB headroom). Pending music is stopped without clearing the queue; MA volume starts at 10% while muted. No song starts automatically.'))
            entries.append(ConfigEntry(key='start_listening',type=ConfigEntryType.ACTION,label='Start listening',action=f'start_line_v1_{hours}',required=False))
        if state=='prepared' and not getattr(connection,'normal',False):
            entries.append(ConfigEntry(key='diagnostic_consent',type=ConfigEntryType.LABEL,label='Pressing Start first check confirms I am beside the amplifier with volume low and approve OUT3/4 only, −60 dB, 120 seconds.'))
            entries.append(ConfigEntry(key='confirm_check',type=ConfigEntryType.ACTION,
                label='Start first check',action='confirm_check',required=False))
        elif state=='stopped':
            entries.append(ConfigEntry(key='prepare_check',type=ConfigEntryType.ACTION,
                label='Prepare first check',action='prepare_check',required=False))
        elif state=='listening':
            entries.append(ConfigEntry(key='listening_note',type=ConfigEntryType.LABEL,
                label=('Listening session active · Fosi is the main volume control; MA is digital attenuation. Fixed unity master / −6 dB headroom; remaining '+str(connection.state.get('seconds_remaining'))+' seconds. Pause holds muted output at most 10 minutes; Stop ends consent.' if getattr(connection,'normal',False) else 'Optional check active · fixed −60 dB, 120 seconds. Start music yourself; not normal listening.')))
        if state=='stopped':entries.append(ConfigEntry(key='diagnostic_boundary',type=ConfigEntryType.LABEL,label='Optional first speaker check below: OUT3/4, −60 dB, 120 seconds. Not required repeatedly for normal listening; software is not acoustic verification.'))
        return tuple(entries)

    async def handle_config_action(self,action):
        scopes={f'start_line_v1_{h}':h for h in (1,2,4)}
        choices={f'duration_{h}':h for h in (1,2,4)}
        if action not in ('verify_session','prepare_check','confirm_check','stop_output') and action not in scopes and action not in choices:raise ActionUnavailable('Unknown fixed speaker-control action')
        if action=='stop_output':
            connection=await Connection.open('')
            try:status=await connection.request('stop')
            finally:await connection.close()
            for owned in list(self._windows.values()):await owned.close()
            if status.get('state')!='stopped':return ConfigActionResult(message='Closing speaker output; final stop verification is pending. Keep Stop available.')
            return ConfigActionResult(message='Speaker output stopped. Pause the music queue separately in Music Assistant.')
        identity=await interactive_identity(self.mass)
        if action=='verify_session':return ConfigActionResult(message='Music Assistant login context verified. No sound authorized.')
        connection=self._windows.get(identity.session_digest)
        if action in choices:
            if connection and not connection.closed:raise ActionUnavailable('Stop the active session before changing scope')
            self._proposals[identity.session_digest]=choices[action]
            return await self.get_config_entries()
        if action=='prepare_check' or action in scopes:
            if connection and not connection.closed:raise ActionUnavailable('This login already owns a speaker check')
            normal=action in scopes
            if normal and scopes[action]!=self._proposals.get(identity.session_digest,2):raise ActionUnavailable('Scope changed; review the displayed Start button again')
            connection=await Connection.open(identity.session_digest)
            connection.normal=normal
            try:
                if normal:
                    await connection.request('normal-prepare',hours=scopes[action],policy='fosi-line-v1')
                else:await connection.request('prepare')
            except Exception:await connection.close();raise
            self._windows[identity.session_digest]=connection
            task=asyncio.create_task(self.watch(connection));self._monitors.add(task);task.add_done_callback(self._monitors.discard)
            for _ in range(40):
                if connection.closed:raise ActionUnavailable('Speaker preparation was stopped')
                if connection.state.get('state')=='prepared' and connection.state.get('handle'):
                    if not normal:return await self.get_config_entries()
                    try:
                        # Native preparation already stopped pending queue before
                        # capture. Volume write is upstream and still zero/muted.
                        await self.mass.players.cmd_volume_set('02:00:00:00:21:01',10)
                        for _ in range(30):
                            status=self.player_readback()
                            if status['volume']==10 and status['state'] in ('idle','off'):break
                            await asyncio.sleep(.1)
                        else:raise ActionUnavailable('Stopped MA volume10 readback failed')
                        await interactive_identity(self.mass)
                        # Powered-off is an exact stopped barrier, but initial
                        # warm readiness must remain until user presses Play.
                        await connection.request('normal-heartbeat',state='idle',volume=10)
                        await connection.request('normal-confirm',handle=connection.state['handle'],generation=connection.state['generation'])
                        for _ in range(100):
                            if connection.closed:raise ActionUnavailable('Listening start cancelled')
                            if connection.state.get('state')=='listening':return await self.get_config_entries()
                            await asyncio.sleep(.1)
                        raise ActionUnavailable('Native listening start failed')
                    except BaseException:await connection.close();raise
                await asyncio.sleep(1)
            await connection.close();raise ActionUnavailable('Silent speaker preparation timed out')
        if not connection or connection.closed or connection.state.get('state')!='prepared':raise ActionUnavailable('Fresh silent preparation required')
        # Explicit second button, from real MA options UI; no role/presence strings
        # accepted from callers. Generation/one-use handle never shown as a code.
        await connection.request('confirm',handle=connection.state['handle'],generation=connection.state['generation'])
        for _ in range(8):
            if connection.closed:raise ActionUnavailable('Attended check was stopped')
            if connection.state.get('state')=='listening':return await self.get_config_entries()
            await asyncio.sleep(.25)
        await connection.close();raise ActionUnavailable('Native attended check did not start')
