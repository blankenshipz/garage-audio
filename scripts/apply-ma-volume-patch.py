"""Build-time exact patch application. No fuzzy offsets, runtime hooks or downloads."""
import hashlib
import json
from pathlib import Path
import re
import sys


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sections(data):
    result = {}
    for section in data.decode().split('diff --git ')[1:]:
        lines = section.splitlines(keepends=True)
        match = re.fullmatch(r'a/(\S+) b/(\S+)\n', lines[0])
        if not match or match[1] != match[2]:
            raise ValueError('renames/unsafe patch header forbidden')
        result[match[1]] = lines[1:]
    return result


def apply_exact(base, lines, positions=None):
    source = base.decode().splitlines(keepends=True)
    result, position, cursor = [], 0, 0
    hunk = 0
    while cursor < len(lines):
        match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', lines[cursor])
        if not match:
            cursor += 1
            continue
        start = positions[hunk]['old'] if positions else int(match[1])-1
        new_start = positions[hunk]['new'] if positions else int(match[3])-1
        hunk += 1
        if start < position:
            raise ValueError('overlapping hunks')
        result.extend(source[position:start]); position = start
        old_count, new_count = int(match[2] or 1), int(match[4] or 1)
        old_seen = new_seen = 0
        if len(result) != new_start:
            raise ValueError('new hunk position mismatch')
        cursor += 1
        while cursor < len(lines) and not lines[cursor].startswith('@@ '):
            line = lines[cursor]; cursor += 1
            if line.startswith('\\ No newline'):
                raise ValueError('unsupported no-newline patch')
            if line[:1] not in (' ', '-', '+'):
                raise ValueError('invalid hunk line')
            text = line[1:]
            if line[0] in ' -':
                if position >= len(source) or source[position] != text:
                    raise ValueError('exact base/context mismatch')
                position += 1; old_seen += 1
            if line[0] in ' +':
                result.append(text); new_seen += 1
        if (old_seen, new_seen) != (old_count, new_count):
            raise ValueError('hunk counts mismatch')
    result.extend(source[position:])
    return ''.join(result).encode()


def main():
    root, manifest_path, patch_path = map(Path, sys.argv[1:])
    manifest = json.loads(manifest_path.read_text())
    data = patch_path.read_bytes()
    if digest(data) != manifest['patch_sha256']:
        raise ValueError('immutable full patch mismatch')
    parts = sections(data)
    if set(parts) != set(manifest['public_diff_files']):
        raise ValueError('unexpected upstream diff inventory')
    planned = {}
    for name, hashes in manifest['runtime_files'].items():
        path = root/name
        if path.is_symlink() or '..' in Path(name).parts:
            raise ValueError('unsafe target')
        base = path.read_bytes()
        if digest(base) != hashes['base']:
            raise ValueError('base hash mismatch: '+name)
        result = apply_exact(base, parts[name], hashes['hunk_positions'])
        if digest(result) != hashes['result']:
            raise ValueError('result hash mismatch: '+name)
        planned[path] = result
    for path, result in planned.items():
        path.write_bytes(result)
    print('Applied exact six-runtime-file upstream patch; full eight-file bytes hash verified')


if __name__ == '__main__':
    main()
