"""Package committed source only; never collect local runtime data."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def forbidden(name: str) -> bool:
    path = PurePosixPath(name)
    blocked = {'.git', 'node_modules', '__pycache__', '.pytest_cache',
               'uploads', 'backups', 'logs', 'email-snapshots', '.venv', 'venv'}
    return (bool(set(path.parts) & blocked)
            or any(part.startswith('.venv') for part in path.parts)
            or ('.env' in path.name and not path.name.endswith('.env.example')
                and path.name != '.env.example')
            or path.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.log',
                                     '.pem', '.key', '.pfx', '.pyc'}
            or path.name.endswith(('-wal', '-shm')))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if git('status', '--porcelain', '--untracked-files=no').strip():
        raise SystemExit('Commit tracked changes before packaging.')
    commit = git('rev-parse', 'HEAD').decode().strip()
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / f'XS-ERP-{stamp}-{commit[:7]}.zip'
    if target.exists():
        raise SystemExit(f'Refusing to overwrite {target}')
    source = zipfile.ZipFile(io.BytesIO(git('archive', '--format=zip', commit)))
    files = [info for info in source.infolist() if not info.is_dir()]
    rejected = [info.filename for info in files if forbidden(info.filename)]
    if rejected:
        raise SystemExit('Unsafe tracked paths; review before delivery: ' + ', '.join(rejected))
    hashes = {}
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as output:
        for info in files:
            data = source.read(info)
            hashes[info.filename] = hashlib.sha256(data).hexdigest()
            info.filename = 'XS-ERP/' + info.filename
            output.writestr(info, data)
        output.writestr('XS-ERP/SOURCE_MANIFEST.json', json.dumps({
            'commit': commit, 'files_sha256': hashes,
            'note': 'Committed source only. No Git history or runtime data.',
        }, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(target) as check:
        assert check.testzip() is None
        for name, digest in hashes.items():
            assert hashlib.sha256(check.read('XS-ERP/' + name)).hexdigest() == digest
    print(json.dumps({'path': str(target.resolve()), 'commit': commit,
                      'source_files': len(hashes), 'bytes': target.stat().st_size,
                      'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
