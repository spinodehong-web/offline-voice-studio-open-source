"""Build the actual native UI with an empty catalog. No ML dependencies or audio generation."""
import argparse
import fcntl
import json
import plistlib
import py_compile
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT/'build'
# Only the macOS version the app was actually developed and run on; no older system is claimed.
MIN_MACOS = '26.2'
SWIFT_TARGET = 'arm64-apple-macos'+MIN_MACOS
PREVIEW_DATA = 'Library/Application Support/Offline Voice Studio Open Source Preview'
# Preview launches add one library request and one run log each time; keep only the newest ones.
PREVIEW_KEEP = 5
# Exactly what this script writes under build/; --rebuild removes only these.
PREVIEW_OUTPUTS = ('OfflineVoiceStudioPreview.app', 'PreviewSwiftModuleCache',
                   'PreviewVoiceStudio.swift', 'preview-worker.py')
# Shared verbatim by the staged preview worker and tests; standard library only.
PRUNE_SOURCE = r'''
def prune_preview_data(folder, keep):
    from pathlib import Path
    import re
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise ValueError('Preview retention must keep at least one file')
    folder = Path(folder)
    expected = Path.home()/'Library/Application Support/Offline Voice Studio Open Source Preview'
    # An environment override or a linked directory must never broaden cleanup.
    if folder.absolute() != expected.absolute() or folder.is_symlink():
        return []
    removed = []
    uuid = r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}'
    for directory, prefix, suffix in ((folder, 'library-request-', 'json'), (folder/'Logs', 'run-', 'log')):
        if directory.is_symlink() or not directory.is_dir():
            continue
        files = []
        for path in directory.glob(prefix+'*.'+suffix):
            try:
                if not path.is_symlink() and path.is_file() and re.fullmatch(re.escape(prefix)+uuid+r'\.'+suffix, path.name):
                    files.append((path.stat().st_mtime, path.name, path))
            except OSError:
                continue
        files.sort(reverse=True)
        for _, _, path in files[keep:]:
            try:
                if directory.is_symlink() or path.is_symlink():
                    continue
                path.unlink()
                removed.append(path.name)
            except OSError:
                pass
    return removed
'''
exec(PRUNE_SOURCE)


def acquire_build_lock(build=BUILD):
    build = Path(build)
    build.mkdir(parents=True, exist_ok=True)
    lock = (build/'.preview-build.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise SystemExit('Another preview build is running; no build files were removed')
    return lock


def ensure_preview_stopped(build=BUILD):
    executable = Path(build)/'OfflineVoiceStudioPreview.app/Contents/MacOS/OfflineVoiceStudio'
    paths = {str(executable.absolute()), str(executable.resolve())}
    try:
        result = subprocess.run(['ps','-ww','-axo','pid=,command='], capture_output=True, text=True, check=True)
    except (OSError, subprocess.SubprocessError):
        raise SystemExit('Cannot check whether the preview is running; no build files were removed')
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and any(parts[1] == path or parts[1].startswith(path+' ') for path in paths):
            raise SystemExit('Preview is running (PID '+parts[0]+'); quit it normally before --rebuild. No files were removed')


def clean_preview_outputs(build=BUILD):
    """Remove only the preview's own outputs; other build/ content (e.g. full-build cache) stays."""
    ensure_preview_stopped(build)
    removed = []
    for name in PREVIEW_OUTPUTS:
        path = Path(build)/name
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            continue
        removed.append(name)
    return removed


def preview_worker_source(source):
    text = source.replace('Application Support/Offline Voice Studio Open Source',
                          'Application Support/Offline Voice Studio Open Source Preview')
    entry = "if __name__=='__main__':main()"
    if text.count(entry) != 1:
        raise SystemExit('Worker entry point changed; inspect before building the preview')
    prune = PRUNE_SOURCE+"\ntry:\n removed=prune_preview_data(DATA,%d)\n if removed:print(json.dumps(dict(event='preview_cleanup',removed=removed)),file=sys.stderr)\nexcept Exception as error:\n print('Preview cleanup skipped: '+str(error),file=sys.stderr)\n" % PREVIEW_KEEP
    return text.replace(entry, "if __name__=='__main__':\n"+''.join(' '+line+'\n' for line in prune.strip('\n').splitlines())+' main()')


def codesign(args):
    # Ad-hoc re-signing the linker-signed binary always prints this harmless notice.
    result = subprocess.run(['codesign']+args, capture_output=True, text=True)
    for line in result.stderr.splitlines():
        if not line.endswith('replacing existing signature'):
            print(line, file=sys.stderr)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, ['codesign']+args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--open', action='store_true', help='Open the built interface preview')
    parser.add_argument('--rebuild', action='store_true', help='Remove the previous preview build first (e.g. after git pull)')
    parser.add_argument('--keep-cache', action='store_true', help='Keep build/PreviewSwiftModuleCache (about 200MB) after building')
    args = parser.parse_args()
    if sys.platform != 'darwin' or sys.version_info < (3, 9):
        raise SystemExit('Interface preview needs macOS and Python3.9+; full TTS uses Python3.12')
    if not shutil.which('swiftc'):
        raise SystemExit('Install Apple command-line tools first: xcode-select --install')
    interpreter = Path(sys.executable).resolve()
    if not interpreter.is_file():
        raise SystemExit('Cannot locate the local Python interpreter')
    _build_lock = acquire_build_lock()
    output = BUILD/'OfflineVoiceStudioPreview.app'
    if args.rebuild:
        clean_preview_outputs()
    if output.exists():
        raise SystemExit('Preview already built. Open build/OfflineVoiceStudioPreview.app, or after source changes run: python3 tools/preview_app.py --rebuild --open')
    resources = output/'Contents/Resources'
    engine = resources/'Engine'
    engine.mkdir(parents=True)
    (output/'Contents/MacOS').mkdir()
    # Developer preview uses a wrapper, not an escaping bundle symlink: strict
    # codesign validation must succeed without signing/changing the user's Python.
    launcher = resources/'Runtime/bin/python3'
    launcher.parent.mkdir(parents=True)
    launcher.write_text('#!/bin/sh\nexec '+shlex.quote(str(interpreter))+' "$@"\n')
    launcher.chmod(0o755)
    for source in (ROOT/'src').glob('*.py'):
        target = engine/(source.stem+'.pyc')
        if source.name == 'worker.py':
            staged = BUILD/'preview-worker.py'
            staged.write_text(preview_worker_source(source.read_text()))
            source = staged
        py_compile.compile(str(source), cfile=str(target), dfile='offline_voice/'+target.stem+'.py', doraise=True)
    for name in ('voice-profile.json','approved-voices.json','voices.json','voice-catalog.json'):
        shutil.copy2(ROOT/'config'/name, engine/name)
    if json.loads((engine/'approved-voices.json').read_text())['voices']:
        raise SystemExit('Interface-only preview requires the repository empty voice catalog')
    shutil.copy2(ROOT/'config/model-manifest.json', resources/'model-manifest.json')
    native = (ROOT/'src/VoiceStudio.swift').read_text()
    native = native.replace('Application Support/Offline Voice Studio Open Source',
                            'Application Support/Offline Voice Studio Open Source Preview')
    title = 'window.title="离线配音工作室"'
    if title not in native or 'intro.font = .systemFont(ofSize:14)' not in native:
        raise SystemExit('Native preview insertion points changed; inspect before building')
    native = native.replace(title,'window.title="离线配音工作室 · 界面预览"')
    native = native.replace('intro.font = .systemFont(ofSize:14)',
        'intro.stringValue="界面预览 · 未配置模型与参考音色\\n可编辑文稿、查找替换、查看项目窗口；此预览不提供配音生成。";intro.font = .systemFont(ofSize:14)')
    staged_native = BUILD/'PreviewVoiceStudio.swift'
    staged_native.write_text(native)
    cache = BUILD/'PreviewSwiftModuleCache'
    cache.mkdir(exist_ok=True)
    subprocess.run(['swiftc','-O','-module-cache-path',str(cache),'-parse-as-library','-target',SWIFT_TARGET,
        str(staged_native),str(ROOT/'src/ProjectEditor.swift'),'-o',str(output/'Contents/MacOS/OfflineVoiceStudio'),
        '-framework','Cocoa','-framework','AVFoundation','-framework','UniformTypeIdentifiers','-framework','CryptoKit'],check=True)
    info = dict(CFBundleName='离线配音工作室 界面预览',CFBundleDisplayName='离线配音工作室 界面预览',
        CFBundleIdentifier='com.spinode.offline-voice-studio.opensource.preview',CFBundleExecutable='OfflineVoiceStudio',
        CFBundlePackageType='APPL',CFBundleShortVersionString='2.3',CFBundleVersion='230',LSMinimumSystemVersion=MIN_MACOS,
        NSHighResolutionCapable=True,NSHumanReadableCopyright='Copyright2026 spinodehong-web. MIT License.')
    (output/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    # Only the generated preview is signed; its external interpreter is unchanged.
    codesign(['--force','--sign','-',str(output)])
    codesign(['--verify','--strict',str(output)])
    if not args.keep_cache:
        # The module cache is only a compiler speed-up; the signed App does not use it.
        shutil.rmtree(cache)
    if args.open:
        subprocess.run(['open',str(output)],check=True)
    print(json.dumps(dict(app=str(output),mode='interface_preview_only',portable=False,
                         models_required=False,private_voices_included=False,audio_generated=False,
                         module_cache_kept=args.keep_cache,
                         data_dir='~/'+PREVIEW_DATA,data_files_kept_per_kind=PREVIEW_KEEP),ensure_ascii=False))


if __name__ == '__main__':
    main()
