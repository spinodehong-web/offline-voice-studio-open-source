"""Make a fixed-reference Base-model import pack from verified local files only."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            result.update(block)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', type=Path, required=True, help='Parent of qwen3-voice-base/')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base = args.models.resolve()
    manifest = json.loads((ROOT/'config/model-manifest.json').read_text())
    selected = {key: row for key, row in manifest.items() if key.startswith('qwen3-voice-base/')}
    if not selected:
        raise ValueError('No Base model entries in manifest')
    for key, expected in selected.items():
        path = (base/key).resolve()
        if not path.is_relative_to(base) or path.stat().st_size != expected['size'] or digest(path) != expected['sha256']:
            raise ValueError('Local model does not match frozen manifest: '+key)
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output/'VoiceModel-Identity.zip'
    if target.exists():
        raise ValueError('Existing model pack is preserved; choose an empty output folder')
    temporary = target.with_suffix('.writing')
    try:
        with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
            for key in selected:
                archive.write(base/key, key)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps(dict(pack=str(target),sha256=digest(target),files=len(selected),downloaded=False)))


if __name__ == '__main__':
    main()
