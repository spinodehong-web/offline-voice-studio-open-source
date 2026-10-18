"""Register an existing, user-authorized reference; no synthesis or networking."""
import argparse
import hashlib
import json
import re
import shutil
import wave
from pathlib import Path


def add_voice(output, key, name, wav, transcript, confirmed):
    if not confirmed:
        raise ValueError('Listening confirmation must come from the person, not this tool')
    if not re.fullmatch(r'local-[a-z0-9][a-z0-9-]{0,63}', key):
        raise ValueError('Use a local- prefix followed by lowercase letters, digits or hyphens')
    if not name.strip() or len(name) > 100 or not transcript.strip() or len(transcript) > 2000:
        raise ValueError('Provide a voice name and the exact reference transcript')
    with wave.open(str(wav), 'rb') as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth(), stream.getcomptype()) != (24000, 1, 2, 'NONE'):
            raise ValueError('Reference must already be 24kHz mono PCM16; original is not converted')
        if not 1 <= stream.getnframes()/24000 <= 30:
            raise ValueError('Use a 1–30 second reference')
    output = Path(output)
    path = output/'approved-voices.json'
    catalog = json.loads(path.read_text()) if path.exists() else dict(scope='local-user-configured-catalog', voices=[])
    if len(catalog['voices']) >= 35 or any(row['id'] == key for row in catalog['voices']):
        raise ValueError('Catalog is full or this ID already exists; existing entries are not replaced')
    profile = dict(id=key, name=name, seed=42, instruction='', language='user-supplied',
                   origin='local-user-owned-reference', approved=True, status='listening_confirmed')
    fingerprint = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
    relative = Path('approved-references')/(key+'.wav')
    target = output/relative
    if target.exists():
        raise ValueError('Destination already exists; original files are preserved')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wav, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    profile['fingerprint'] = fingerprint
    catalog['voices'].append(dict(id=key, profile=profile, profile_hash=fingerprint,
        reference=dict(path=relative.as_posix(), sha256=digest, text=transcript.strip()), auditions=[],
        confirmation_source='explicit_local_user_flag; not an automated listening result'))
    temporary = path.with_suffix('.writing')
    temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temporary.replace(path)
    return dict(voice_id=key, reference_sha256=digest, catalog=str(path), synthesized=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--id', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--wav', type=Path, required=True)
    parser.add_argument('--text-file', type=Path, required=True)
    parser.add_argument('--listening-confirmed', action='store_true')
    args = parser.parse_args()
    print(json.dumps(add_voice(args.output, args.id, args.name, args.wav,
        args.text_file.read_text(encoding='utf-8'), args.listening_confirmed), ensure_ascii=False))


if __name__ == '__main__':
    main()
