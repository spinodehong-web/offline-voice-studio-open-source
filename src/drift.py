"""Explicit broad volume-drift compensation; preserves short-term performance dynamics."""
import argparse,json
from pathlib import Path
import numpy as np
import soundfile as sf


def speech_thirds(signal,rate):
    frame=round(rate*.04);hop=round(rate*.02)
    rms=np.array([np.sqrt(np.mean(signal[i:i+frame].astype('float64')**2)) for i in range(0,len(signal)-frame,hop)])
    levels=[]
    for part in np.array_split(rms,3):
        active=part[part>max(.001,np.percentile(part,35))]
        if not len(active):raise ValueError('录音有效语音不足，不能估算音量趋势')
        levels.append(float(20*np.log10(np.sqrt(np.mean(active**2))+1e-12)))
    return levels


def compensate(signal,rate,desired_drop_db=1.5,max_boost_db=6):
    x=np.asarray(signal,dtype='float32')
    if x.ndim!=1 or len(x)<rate*8 or not np.isfinite(x).all():raise ValueError('需要至少8秒的有效单声道录音')
    before=speech_thirds(x,rate);duration=len(x)/rate
    # One smooth contour over most of the recording, never tracking words or breaths.
    phase=np.clip((np.arange(len(x))/rate-duration*.12)/(duration*.76),0,1)
    contour=.5-.5*np.cos(np.pi*phase)
    def render(boost):return x*np.power(10,boost*contour/20)
    boost=0.
    if before[0]-before[-1]>desired_drop_db:
        lo,hi=0.,float(max_boost_db)
        for _ in range(14):
            mid=(lo+hi)/2;levels=speech_thirds(render(mid),rate)
            if levels[0]-levels[-1]>desired_drop_db:lo=mid
            else:hi=mid
        boost=hi
    result=render(boost).astype('float32');peak=float(np.max(np.abs(result)))
    headroom=min(1.,.84/max(peak,1e-9));result*=headroom
    return result,{'before_speech_dbfs_thirds':before,'after_speech_dbfs_thirds':speech_thirds(result,rate),'maximum_contour_boost_db':boost,'static_headroom_gain':headroom,'method':'Single slow cosine gain contour; no compression, per-sentence normalization, gate, denoise, EQ, pitch or time change','duration_unchanged':len(result)==len(x)}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    x,sr=sf.read(a.source,dtype='float32');y,r=compensate(x,sr);a.output.parent.mkdir(parents=True,exist_ok=True);sf.write(a.output,y,sr,subtype='PCM_16');a.output.with_suffix('.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
