"""Read-only Linux target metadata; never opens /dev/snd (including permissions tests)."""
import copy
import json
from pathlib import Path
from unittest.mock import patch
from garage_audio.audio_device import inventory, validate_inventory, IDENTITY, CAPABILITIES

value = inventory(nodes=Path('/host-snd'))
assert value['identity'] == IDENTITY and value['capabilities'] == CAPABILITIES
assert value['nodes']['pcmC1D0p']['mode'] == 0o600
assert value['nodes']['pcmC1D0p']['uid'] == 999  # current old owner, untouched
try:
    validate_inventory(value, container_nodes=Path('/host-snd'))
except PermissionError:
    print('PASS actual_linux_metadata_rejects_old_UID999_without_open')
else:
    raise AssertionError('old owner admitted')
for key, replacement in [('serial', 'NOT_THIS_UMC'), ('product', 'ffff')]:
    bad = copy.deepcopy(value)
    bad['identity'][key] = replacement
    try:
        validate_inventory(bad, container_nodes=Path('/host-snd'))
    except ValueError:
        print('PASS synthetic_changed_'+key+'_based_on_actual_metadata_rejected')
    else:
        raise AssertionError('changed identity admitted')
print(json.dumps(value, sort_keys=True))
print('NO_DAC_OPEN; NO_USB_UNPLUG; NO_PERMISSION_CHANGE; reconnect identity changes separately synthetic')
