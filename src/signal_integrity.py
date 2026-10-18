"""Waveform defect screening only; never a naturalness/listening verdict."""
import numpy as np
import soundfile as sf
VERSION='scale-invariant-flat-top-v1'
def analyze(audio):
 x=np.asarray(audio,dtype=np.float32)
 if x.ndim==1:x=x[:,None]
 if x.ndim!=2 or len(x)<2 or not np.isfinite(x).all():return dict(version=VERSION,finite=False,suspected_clipping=True,signal_integrity_pass=False,channels=[])
 channels=[]
 for i in range(x.shape[1]):
  y=x[:,i];peak=float(np.max(np.abs(y)))
  if peak<1e-5:channels.append(dict(peak=peak,near_peak_fraction=0,flat_top_fraction=0,suspected_clipping=False));continue
  # Nonzero equal adjacent samples near the channel peak survive volume changes.
  near=float(np.mean(np.abs(y)>=peak*.999));flat=float(np.mean((np.abs(np.diff(y))<=1e-7)&(np.abs(y[:-1])>=peak*.9)));full=float(np.mean(np.abs(y)>=.999))
  suspect=full>0 or flat>=.005 or near>=.02
  channels.append(dict(peak=peak,near_peak_fraction=near,flat_top_fraction=flat,full_scale_fraction=full,suspected_clipping=bool(suspect)))
 suspect=any(c['suspected_clipping'] for c in channels)
 return dict(version=VERSION,finite=True,suspected_clipping=suspect,signal_integrity_pass=not suspect,channels=channels,thresholds=dict(flat_top_fraction=.005,near_peak_fraction=.02),perceptual_acceptance=False)
def inspect(path):
 audio,sr=sf.read(path,dtype='float32');return analyze(audio)|dict(sample_rate=sr,sample_frames=len(audio))
