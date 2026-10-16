"""Executable pinned-client/model compatibility only; no server/auth/provider/network calls."""
import asyncio
import inspect
import logging
import socket
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from music_assistant_models.config_entries import ConfigEntry, ProviderConfig
from music_assistant_models.enums import ConfigEntryType, ProviderType
from garage_audio import spotify_setup


async def check():
    # Socket block is an isolated test guard, not a replacement provider/client.
    with patch.object(socket.socket,'connect',side_effect=AssertionError('No server connection authorized')):
        for name in ('music_assistant_client','music_assistant_client.connection'):
            logging.getLogger(name).setLevel(logging.DEBUG)
        ma=spotify_setup.client('synthetic-local-contract-not-a-real-token')
        assert hasattr(ma,'send_command')
        assert hasattr(ma.players,'fetch_state') and hasattr(ma.players,'players')
        assert {'provider_domain','include_values'}<=set(inspect.signature(ma.config.get_provider_configs).parameters)
        assert {'provider_domain','values','instance_id'}<=set(inspect.signature(ma.config.save_provider_config).parameters)
        conf=ProviderConfig(type=ProviderType.PLUGIN,domain='spotify_connect',instance_id='synthetic-owned',
            values={'connected_players':ConfigEntry(key='connected_players',type=ConfigEntryType.STRING,
                                                   value=[spotify_setup.fixed_player()])})
        assert spotify_setup.selected_players(conf)==[spotify_setup.fixed_player()]
        result=spotify_setup.summary(conf)
        assert result['player_bound'] is True and result['runtime_verified'] is False and result['sound_authority'] is False
        assert logging.getLogger('music_assistant_client.connection').disabled
        assert logging.getLogger('music_assistant_client').disabled
    print('Real pinned MA1.6.0 client/config model compatibility verified locally; zero server/auth/provider/network/music calls.')


if __name__=='__main__': asyncio.run(check())
