"""Approved offline voice pacing and slow drift guard; no cloud calls."""
import json,re,subprocess
from pathlib import Path
import numpy as np
import soundfile as sf


def finish(signal,rate,output,config,resources):
    FFMPEG=str(resources/'bin/ffmpeg')
    from cadence import shape_sentence_pauses
    from drift import compensate,speech_thirds
    output=Path(output);raw=output.with_suffix('.generated.wav');sf.write(raw,signal,rate,subtype='PCM_16')
    settings=config.get('pacing',{});tempo=settings.get('tempo',1.1556521739130432)
    paced=output.with_suffix('.paced.wav')
    # FFmpeg atempo=1 is not PCM-transparent: bypass stretch at exactly normal speed.
    filters=['adelay=180:all=1','apad=pad_dur=0.18']
    if tempo != 1.0:filters.append(f'atempo={tempo}')
    filters.append('atrim=start=0.12')
    subprocess.run([FFMPEG,'-y','-i',str(raw),'-af',','.join(filters),'-c:a','pcm_s16le',str(paced)],capture_output=True,check=True)
    signal,rate=sf.read(paced,dtype='float32')
    pauses=[]
    if not settings.get('preserve_paragraph_gaps'):
        from faster_whisper import WhisperModel
        asr=WhisperModel(str(resources/'Models/asr-small'),device='cpu',compute_type='int8',cpu_threads=4,local_files_only=True)
        from scipy.signal import resample_poly
        from math import gcd
        divisor=gcd(rate,16000)
        asr_pcm=resample_poly(signal,16000//divisor,rate//divisor).astype('float32')
        segments,_=asr.transcribe(asr_pcm,language='en',beam_size=5,word_timestamps=True)
        words=[{'text':w.word,'start':w.start,'end':w.end} for s in segments for w in(s.words or [])]
        # Upper bound only: do not lengthen the model's naturally short pauses.
        targets={}
        for a,b in zip(words,words[1:]):
            if re.search('[.!?]$',a['text'].strip()):targets[a['text'].strip()]=min(b['start']-a['end'],settings.get('sentence_gap',.65))
        signal,pauses=shape_sentence_pauses(signal,rate,words,max_gap=4,targets=targets)
    guard=config.get('level_guard',{});repair=None
    if guard.get('enabled') and len(signal)>=rate*8:
        try:levels=speech_thirds(signal,rate)
        except ValueError as e:levels=None;repair={'applied':False,'reason':str(e)}
        if levels is not None and levels[0]-levels[-1]>guard.get('minimum_drop_db',4):
            sf.write(output.with_suffix('.before-level-repair.wav'),signal,rate,subtype='PCM_16')
            signal,repair=compensate(signal,rate,guard.get('retain_drop_db',1.5),guard.get('max_boost_db',6))
    return signal,rate,{'tempo':tempo,'tempo_filter_bypassed':tempo == 1.0,'pause_edits':pauses,'level_drift_repair':repair,'preset_version':config.get('version'),'emotion':'text-led model performance; no per-sentence emotion template'}
