#!/bin/sh
# Run only from a private staged source directory; retain every failed attempt.
set -eu
project=$1
case "$project" in gaudio-m2-*) ;; *) exit 64;; esac
test "$(df -B1 --output=avail / | tail -1)" -ge 2415919104
test "$(docker image inspect garage-audio-dashboard:connect-r20-b0e3c8fa --format '{{.Id}}')" = sha256:44038332e66f37ddca6a49e049a9c818e3392c06b8f073e29cedaed3e28b82f6
# Local tag is byte-verified before FROM; Docker cannot use a bare image config ID in FROM.
export AUDIO_IMAGE=garage-audio-audio:$project
docker build --build-arg BASE_IMAGE=garage-audio-dashboard:connect-r20-b0e3c8fa -f Dockerfile.audio -t "$AUDIO_IMAGE" .
test "$(df -B1 --output=avail / | tail -1)" -ge 2415919104
chmod 755 tests
chmod 644 tests/audio_ma_fixture.py tests/audio_container_journey.py
python3 tests/run_audio_container_file.py "$project" "$PWD/receipt"
test "$(df -B1 --output=avail / | tail -1)" -ge 2147483648
docker image inspect "$AUDIO_IMAGE" --format '{{.Id}}'
