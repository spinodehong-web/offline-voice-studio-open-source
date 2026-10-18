"""Offline ASR and learned speaker-similarity screening; never a listening verdict."""
import json,re
from difflib import SequenceMatcher
from math import gcd
from pathlib import Path
import numpy as np
import soundfile as sf
from signal_integrity import analyze as signal_integrity

def normalized(text):
 # Keep lexical boundaries and contractions: "a part" must not equal "apart".
 # Case and spoken punctuation remain irrelevant; apostrophe spelling is retained.
 text=text.lower().replace('’',"'")
 if re.search(r'[\u3400-\u9fff]',text):return ' '.join(re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)*|[^\W_]",text))
 return ' '.join(re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)*",text))
def text_differences(expected,recognized):
 """Unmodified decoded words vs script; boundaries are diagnostics, never truth labels."""
 want=normalized(expected).split();heard=normalized(recognized).split();differences=[]
 for kind,a,b,c,d in SequenceMatcher(None,want,heard,autojunk=False).get_opcodes():
  if kind!='equal':differences.append(dict(kind=kind,expected_word_start=a,expected_word_end=b,recognized_word_start=c,recognized_word_end=d,expected_words=want[a:b],recognized_words=heard[c:d],status='unconfirmed_asr_difference'))
 return differences
class QualityCheck:
 def __init__(self,resources):
  self.resources=Path(resources);self.asr=None;self.encoder=None
 def sample(self,path,expected,progress=None,condition_on_previous_text=True):
  from faster_whisper import WhisperModel
  from scipy.signal import resample_poly
  if self.asr is None:self.asr=WhisperModel(str(self.resources/'Models/asr-small'),device='cpu',compute_type='int8',cpu_threads=4,local_files_only=True)
  audio,sr=sf.read(path,dtype='float32');div=gcd(sr,16000);pcm=resample_poly(audio,16000//div,sr//div).astype('float32')
  language='zh' if re.search(r'[\u3400-\u9fff]',expected) else 'en'
  segments,_=self.asr.transcribe(pcm,language=language,beam_size=5,condition_on_previous_text=condition_on_previous_text)
  decoded=[];asr_segments=[]
  for segment in segments:
   decoded.append(segment.text.strip())
   asr_segments.append(dict(start=getattr(segment,'start',None),end=segment.end,text=segment.text))
   if progress:progress(min(segment.end,len(audio)/sr),len(audio)/sr)
  recognized=' '.join(decoded).strip();score=SequenceMatcher(None,normalized(expected),normalized(recognized)).ratio()
  finite=bool(np.isfinite(audio).all());peak=float(np.max(np.abs(audio)));clipped=float(np.mean(np.abs(audio)>=.999));duration=len(audio)/sr
  integrity=signal_integrity(audio)
  return dict(text_differences=text_differences(expected,recognized),asr_segments=asr_segments,asr_configuration=dict(model='asr-small',language=language,beam_size=5,condition_on_previous_text=condition_on_previous_text,initial_prompt=None),text_status='asr_exact_match' if normalized(expected)==normalized(recognized) else 'asr_discrepancy_unresolved',spoken_error_confirmed=False,actual_listening_performed=False,signal_integrity=integrity,expected=expected,recognized=recognized,text_similarity=score,seconds=duration,peak=peak,clipped_fraction=clipped,technical_pass=integrity['signal_integrity_pass'] and finite and clipped==0 and peak>.01 and duration>2 and normalized(expected)==normalized(recognized))
 def embedding(self,path):
  import mlx.core as mx
  from mlx_audio.tts.models.chatterbox_turbo.models.voice_encoder.voice_encoder import VoiceEncoder
  from scipy.signal import resample_poly
  if self.encoder is None:
   self.encoder=VoiceEncoder();self.encoder.load_weights(str(self.resources/'Models/voice-identity/encoder.safetensors'),strict=True);mx.eval(self.encoder.parameters())
  audio,sr=sf.read(path,dtype='float32');div=gcd(sr,16000);pcm=resample_poly(audio,16000//div,sr//div).astype('float32')
  vector=np.asarray(self.encoder.embeds_from_wavs([pcm],sample_rate=16000,as_spk=True,trim_top_db=None),dtype='float32').reshape(-1)
  if not np.isfinite(vector).all() or np.linalg.norm(vector)<.01:raise ValueError('说话人特征提取失败')
  return vector/np.linalg.norm(vector)
 def evidence(self,samples,library,key):
  vectors=[self.embedding(s['path']) for s in samples];centroid=np.mean(vectors,axis=0);centroid/=np.linalg.norm(centroid)
  consistency=float(np.dot(vectors[0],vectors[1]));nearest=None
  # Compact float32 index avoids rereading all historical JSON for every new voice.
  rows=library.db.execute('SELECT voice_id,audition_id,vector FROM speaker_vectors WHERE voice_id!=?',(key,)).fetchall()
  if rows:
   matrix=np.stack([np.frombuffer(row['vector'],dtype='float32') for row in rows]);similarities=matrix@centroid;index=int(np.argmax(similarities))
   nearest=dict(voice_id=rows[index]['voice_id'],audition_id=rows[index]['audition_id'],cosine=float(similarities[index]))
  return dict(samples=samples,technical_pass=all(s['technical_pass'] for s in samples) and consistency>=.70,speaker_centroid=centroid.tolist(),cross_text_speaker_cosine=consistency,minimum_consistency=.70,nearest=nearest,similarity_flagged=bool(nearest and nearest['cosine']>=.88),similarity_screen_threshold=.88,similarity_method='Chatterbox pretrained 256-dimensional speaker encoder cosine; heuristic screening only, not proof of identity uniqueness',actual_listening_performed=False,stable_identity_guaranteed=False)
