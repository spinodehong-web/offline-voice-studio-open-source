"""Build the actual native UI with an empty catalog. No ML dependencies or audio generation."""
import argparse
import json
import plistlib
import py_compile
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--open', action='store_true', help='Open the built interface preview')
    args = parser.parse_args()
    if sys.platform != 'darwin' or sys.version_info < (3, 9):
        raise SystemExit('Interface preview needs macOS and Python3.9+; full TTS uses Python3.12')
    if not shutil.which('swiftc'):
        raise SystemExit('Install Apple command-line tools first: xcode-select --install')
    interpreter = Path(sys.executable).resolve()
    if not interpreter.is_file():
        raise SystemExit('Cannot locate the local Python interpreter')
    output = ROOT/'build/OfflineVoiceStudioPreview.app'
    if output.exists():
        raise SystemExit('Preview already built. Open build/OfflineVoiceStudioPreview.app; source changes need a new empty build directory.')
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
            staged = ROOT/'build/preview-worker.py'
            staged.write_text(source.read_text().replace('Application Support/Offline Voice Studio Open Source',
                                                        'Application Support/Offline Voice Studio Open Source Preview'))
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
    staged_native = ROOT/'build/PreviewVoiceStudio.swift'
    staged_native.write_text(native)
    cache = ROOT/'build/PreviewSwiftModuleCache'
    cache.mkdir(exist_ok=True)
    subprocess.run(['swiftc','-O','-module-cache-path',str(cache),'-parse-as-library','-target','arm64-apple-macos14.0',
        str(staged_native),str(ROOT/'src/ProjectEditor.swift'),'-o',str(output/'Contents/MacOS/OfflineVoiceStudio'),
        '-framework','Cocoa','-framework','AVFoundation','-framework','UniformTypeIdentifiers','-framework','CryptoKit'],check=True)
    info = dict(CFBundleName='离线配音工作室 界面预览',CFBundleDisplayName='离线配音工作室 界面预览',
        CFBundleIdentifier='com.spinode.offline-voice-studio.opensource.preview',CFBundleExecutable='OfflineVoiceStudio',
        CFBundlePackageType='APPL',CFBundleShortVersionString='2.3',CFBundleVersion='230',LSMinimumSystemVersion='26.2',
        NSHighResolutionCapable=True,NSHumanReadableCopyright='Copyright2026 spinodehong-web. MIT License.')
    (output/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    # Only the generated preview is signed; its external interpreter is unchanged.
    subprocess.run(['codesign','--force','--sign','-',str(output)],check=True)
    subprocess.run(['codesign','--verify','--strict',str(output)],check=True)
    if args.open:
        subprocess.run(['open',str(output)],check=True)
    print(json.dumps(dict(app=str(output),mode='interface_preview_only',portable=False,
                         models_required=False,private_voices_included=False,audio_generated=False),ensure_ascii=False))


if __name__ == '__main__':
    main()
