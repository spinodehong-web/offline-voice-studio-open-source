"""Assemble a source build from explicitly provided local runtime/resources; never download."""
import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Same tested minimum as tools/preview_app.py; compile target and Info.plist must agree.
MIN_MACOS = '26.2'
SWIFT_TARGET = 'arm64-apple-macos'+MIN_MACOS


def run(args):
    subprocess.run([str(x) for x in args], check=True)


def copy(source, target):
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if target.exists():
            raise ValueError('Will not merge or overwrite an existing resource directory')
        # APFS clones avoid duplicating immutable runtime resources when supported.
        run(['/bin/cp', '-cR', source, target])
    else:
        shutil.copy2(source, target)


def catalog_files(folder):
    folder = Path(folder).resolve()
    path = folder/'approved-voices.json'
    catalog = json.loads(path.read_text())
    voices = catalog.get('voices')
    if not isinstance(voices, list) or len(voices) > 35:
        raise ValueError('Catalog must contain up to35 local voices')
    files, ids = {Path('approved-voices.json')}, set()
    for row in voices:
        if row['id'] in ids or row['profile']['id'] != row['id']:
            raise ValueError('Voice IDs must be unique and match their profiles')
        ids.add(row['id'])
        for asset in [row['reference']] + row.get('auditions', []):
            relative = Path(asset['path'])
            if relative.is_absolute() or '..' in relative.parts or relative.parts[0] not in ('approved-references', 'approved-auditions'):
                raise ValueError('Only local reference/audition assets may be copied')
            resolved = (folder/relative).resolve()
            if not resolved.is_relative_to(folder) or not resolved.is_file():
                raise ValueError('Asset must remain inside the supplied voice folder')
            if hashlib.sha256(resolved.read_bytes()).hexdigest() != asset['sha256']:
                raise ValueError('Local audio checksum does not match its declaration')
            files.add(relative)
    return sorted(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--voices', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    runtime, assets, output = [p.resolve() for p in (args.runtime, args.assets, args.output)]
    if output.exists() or output.suffix != '.app':
        raise ValueError('Output must be a new.app directory; no existing App is modified')
    if not (runtime/'bin/python3').is_file() or not (runtime/'lib/python3.12').is_dir():
        raise ValueError('Supply a relocatable Python3.12 runtime with dependencies already installed')
    for resource in ('bin/ffmpeg', 'Models/asr-small', 'Licenses'):
        if not (assets/resource).exists():
            raise ValueError('Missing supplied resource: '+resource)
    if not os.access(assets/'bin/ffmpeg', os.X_OK):
        raise ValueError('Supplied FFmpeg is not executable')
    voice_files = catalog_files(args.voices) if args.voices else []
    resources = output/'Contents/Resources'
    engine = resources/'Engine'
    engine.mkdir(parents=True)
    (output/'Contents/MacOS').mkdir()
    copy(runtime, resources/'Runtime')
    for name in ('bin/ffmpeg', 'Models/asr-small', 'Licenses'):
        copy(assets/name, resources/name)
    for path in (ROOT/'licenses').iterdir():
        copy(path, resources/'Licenses/Application-Source-Notices'/path.name)
    copy(ROOT/'LICENSE', resources/'Licenses/Application-MIT.txt')
    for name in ('voice-profile.json','voices.json','voice-catalog.json','approved-voices.json'):
        copy(ROOT/'config'/name, engine/name)
    copy(ROOT/'config/model-manifest.json', resources/'model-manifest.json')
    for relative in voice_files:
        copy(Path(args.voices)/relative, engine/relative)
    python = resources/'Runtime/bin/python3'
    for source in sorted((ROOT/'src').glob('*.py')):
        target = engine/(source.stem+'.pyc')
        code = 'import py_compile;py_compile.compile('+repr(str(source))+',cfile='+repr(str(target))+',dfile='+repr('offline_voice/'+source.name)+',doraise=True)'
        run([python, '-I', '-c', code])
    module_cache = ROOT/'build/SwiftModuleCache'
    module_cache.mkdir(parents=True, exist_ok=True)
    run(['swiftc','-O','-module-cache-path',module_cache,'-parse-as-library','-target',SWIFT_TARGET,
         ROOT/'src/VoiceStudio.swift',ROOT/'src/ProjectEditor.swift','-o',output/'Contents/MacOS/OfflineVoiceStudio',
         '-framework','Cocoa','-framework','AVFoundation','-framework','UniformTypeIdentifiers','-framework','CryptoKit'])
    info = dict(CFBundleName='离线配音工作室 开源版',CFBundleDisplayName='离线配音工作室 开源版',
        CFBundleIdentifier='com.spinode.offline-voice-studio.opensource',CFBundleExecutable='OfflineVoiceStudio',
        CFBundlePackageType='APPL',CFBundleShortVersionString='2.3',CFBundleVersion='230',LSMinimumSystemVersion=MIN_MACOS,
        NSHighResolutionCapable=True,NSHumanReadableCopyright='Copyright2026 spinodehong-web. MIT License.')
    (output/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    run(['codesign','--force','--deep','--sign','-',output])
    run(['codesign','--verify','--deep','--strict',output])
    print(json.dumps(dict(app=str(output),version='2.3',voice_catalog='provided locally' if args.voices else 'empty',
                         downloaded=False,model_imported=False,inference_started=False), ensure_ascii=False))


if __name__ == '__main__':
    main()
