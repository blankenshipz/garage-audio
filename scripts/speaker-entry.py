"""Immutable -I entry for root phone controller; no mutable project imports."""
import sys
sys.path.insert(0,'/usr/local/libexec/garage-audio')
from garage_audio.speaker_controller import main
main()
