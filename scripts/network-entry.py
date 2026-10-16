"""Immutable -I entry for root NETWORK-only operator; no project imports."""
import sys
sys.path.insert(0,'/usr/local/libexec/garage-audio-network')
from garage_audio.audio_boot import main
main()
