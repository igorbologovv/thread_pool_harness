"""Extract only data/metadata into a new tree; never execute archived code."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import shutil
import tarfile
import zipfile


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def extract(archives, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    inventory = []
    for path in sorted(Path(archives).iterdir()):
        if not (path.name.endswith('.zip') or path.name.endswith('.tar.gz')):
            continue
        target = destination / path.name.replace('.tar.gz', '').replace('.zip', '')
        target.mkdir()
        record = dict(archive=path.name, sha256=digest(path), members=[], selected=[])
        if path.suffix == '.zip':
            archive = zipfile.ZipFile(path)
            entries = [(i.filename, i.file_size, not i.is_dir(), i) for i in archive.infolist()]
            opener = archive.open
        else:
            archive = tarfile.open(path)
            entries = [(i.name, i.size, i.isfile(), i) for i in archive.getmembers()]
            opener = archive.extractfile
        with archive:
            for name, size, regular, entry in entries:
                record['members'].append(dict(name=name, bytes=size))
                parts = PurePosixPath(name)
                if parts.is_absolute() or '..' in parts.parts:
                    raise ValueError(f'Unsafe archive member: {name}')
                if not regular or parts.suffix not in ('.csv', '.json', '.toml', '.sqlite3'):
                    continue
                output = target.joinpath(*parts.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                with opener(entry) as source, output.open('wb') as sink:
                    shutil.copyfileobj(source, sink)
                record['selected'].append(dict(name=name, sha256=digest(output)))
        inventory.append(record)
    (destination / 'inventory.json').write_text(json.dumps(inventory, indent=2)+'\n')
    return inventory
