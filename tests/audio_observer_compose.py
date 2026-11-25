import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def service_block(text, name):
    lines = text.splitlines()
    out, inside = [], False
    for line in lines:
        if re.match(r'^  [A-Za-z0-9_-]+:\s*$', line):
            inside = line.strip() == name + ':'
            continue
        if inside:
            out.append(line)
    return out


class AudioObserverComposeTests(unittest.TestCase):
    def setUp(self):
        self.block = service_block((ROOT / 'compose.audio-boot.yaml').read_text(), 'audio-observer')
        self.assertTrue(self.block)
        src = (ROOT / 'garage_audio/audio_observer.py').read_text()
        m = re.search(r"DecisionTrace\(\s*'([^']+)'", src)
        self.assertIsNotNone(m)
        self.trace_path = pathlib.PurePosixPath(m.group(1))

    def tmpfs_targets(self):
        targets = []
        for line in self.block:
            m = re.match(r'^    tmpfs:\s*\[(.*)\]\s*$', line)
            if m:
                for item in m.group(1).split(','):
                    item = item.strip().strip('"\'')
                    if item:
                        targets.append((item.split(':')[0], item))
        return targets

    def test_read_only_root(self):
        self.assertIn('    read_only: true', self.block)

    def test_tmp_tmpfs_writable(self):
        mounts = dict(self.tmpfs_targets())
        self.assertIn('/tmp', mounts)
        self.assertIn('rw', mounts['/tmp'].split(':', 1)[1].split(','))

    def test_decision_trace_dir_is_tmpfs(self):
        parent = self.trace_path.parent
        covered = [t for t, _ in self.tmpfs_targets()
                   if parent == pathlib.PurePosixPath(t) or pathlib.PurePosixPath(t) in parent.parents]
        self.assertTrue(covered, 'decision trace directory %s not on a tmpfs' % parent)


if __name__ == '__main__':
    unittest.main()
