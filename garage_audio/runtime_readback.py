"""Read-only feature detection; a legacy status is never configuration proof."""
from . import audio_public


def configuration():
    try:
        result = audio_public.request('audio.config.get')
        return {**result, 'configuration_readback_available': True}
    except audio_public.AudioFailure as error:
        # Keep peer/transport failures intact. A rejected configuration read may
        # still allow a separate status read; it grants no new operation.
        if error.code != 'audio_operation_rejected': raise
        status = audio_public.request('audio.status')
        return {'configuration_readback_available': False, 'configuration': None,
                'revision': None, 'desired': None, 'accepted_for_next_play': None,
                'previous': None, 'loaded': None, 'load_verification': 'unknown',
                'binding': {'kind': status['device_mode'], 'wiring': 'unverified'},
                'observed_status': status, 'hardware_apply_available': False,
                'unavailable_reason': 'Worker rejects configuration readback; status is available but accepted/loaded configuration is unknown. Keep playback unchanged; a matching configuration protocol is required before correction apply or verification.'}
