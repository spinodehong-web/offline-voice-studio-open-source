"""Local multi-role narration projects. Existing voices only, immutable audio takes."""
import json,re,math,hashlib,uuid,os,unicodedata,signal
from pathlib import Path
from studio_release import digest,write_json,approved_voice,catalog
LANGUAGES={'english':'en','chinese':'zh','french':'fr','german':'de','italian':'it','portuguese':'pt','spanish':'es','japanese':'ja','korean':'ko','russian':'ru'}

def number(value,lo,hi,label):
 if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not lo<=value<=hi:raise ValueError(label+'超出范围')
 return float(value)

def validate(project,engine):
 if not isinstance(project,dict) or project.get('schema_version')!=1:raise ValueError('不支持的项目版本')
 roles=project.get('roles');clips=project.get('clips');rules=project.get('dictionary',[])
 if not isinstance(roles,list) or not 1<=len(roles)<=35 or not isinstance(clips,list) or len(clips)>100:raise ValueError('角色或片段数量无效')
 allowed={v['id'] for v in catalog(engine)['voices']};ids=set()
 for role in roles:
  if not isinstance(role,dict) or not isinstance(role.get('id'),str) or not role['id'] or role['id'] in ids or role.get('voice_id') not in allowed:raise ValueError('角色必须使用已确认声音且编号唯一')
  ids.add(role['id'])
 seen=set();count=0
 for c in clips:
  if not isinstance(c,dict) or not isinstance(c.get('id'),str) or not c['id'] or c['id'] in seen:raise ValueError('片段编号无效或重复')
  seen.add(c['id'])
  if not isinstance(c.get('imported',False),bool):raise ValueError('导入片段标记无效')
  if c.get('role_id') not in ids or c.get('language') not in LANGUAGES:raise ValueError('片段角色或语言无效')
  text=c.get('text','')
  if not isinstance(text,str) or '\0' in text or len(text)>2000:raise ValueError('单片段最多2000字符，不能含无效字符')
  if not c.get('imported',False) and not text.strip():raise ValueError('配音片段文稿不能为空')
  count+=len(text);number(c.get('speed',1),.9,1.2,'语速');number(c.get('start',0),0,3600,'时间线位置');number(c.get('trim_in',0),0,3600,'起始裁剪');number(c.get('trim_out',0),0,3600,'结尾裁剪');number(c.get('gain_db',0),-24,6,'片段音量')
  take=c.get('take',0)
  if isinstance(take,bool) or not isinstance(take,int) or not 0<=take<=10000:raise ValueError('重录次数无效')
 if count>12000:raise ValueError('项目文稿合计最多12000字符')
 if not isinstance(rules,list) or len(rules)>500:raise ValueError('发音词典最多500条')
 keys=set()
 for r in rules:
  if not isinstance(r,dict) or not all(isinstance(r.get(k),str) and r[k].strip() and '\0' not in r[k] and len(r[k])<=200 for k in ['source','spoken']):raise ValueError('词典原词和读法均需填写，最多200字符')
  lang=r.get('language','*')
  if lang!='*' and lang not in LANGUAGES:raise ValueError('词典语言无效')
  key=(lang,r['source'].casefold())
  if key in keys:raise ValueError('同语言的词典原词不能重复')
  keys.add(key)
 validate_chapters(project)
 return project

def spoken_text(text,language,rules):
 # Longest literal match, whole Latin words, one pass (no chained replacement).
 active=sorted((r for r in rules if r.get('language','*') in ('*',language)),key=lambda r:(r.get('language')!=language,-len(r['source'])))
 chosen={}
 for r in active:chosen.setdefault(r['source'].casefold(),r)
 if not chosen:return text
 ordered=sorted(chosen.values(),key=lambda r:-len(r['source']))
 patterns=[]
 for r in ordered:
  source=r['source'];latin=bool(re.fullmatch(r"[A-Za-z0-9 ._'’-]+",source))
  patterns.append((r,re.compile((r'(?<!\w)' if latin else '')+re.escape(source)+(r'(?!\w)' if latin else ''),re.IGNORECASE)))
 edits=[];cursor=0
 while cursor<len(text):
  matches=[(m.start(),-len(m.group()),r,m) for r,p in patterns if (m:=p.search(text,cursor))]
  if not matches:break
  _,_,r,m=min(matches,key=lambda x:(x[0],x[1]));edits.append(text[cursor:m.start()]);edits.append(r['spoken']);cursor=m.end()
 edits.append(text[cursor:]);return ''.join(edits)

def binding(c,project):
 role=next(r for r in project['roles'] if r['id']==c['role_id']);spoken=spoken_text(c['text'],c['language'],project.get('dictionary',[]))
 if not 1<=len(spoken)<=4000:raise ValueError('应用发音词典后片段长度无效')
 return dict(voice_id=role['voice_id'],text=c['text'],spoken=spoken,language=c['language'],speed=c.get('speed',1),take=c.get('take',0),pipeline='project-studio-v1')

def speech_pieces(text,limit=220):
 if re.search(r'[\u3400-\u9fff]',text):
  from studio_release import split_chinese
  return [s['text'] for s in split_chinese(text,limit=min(limit,600))]
 if re.search(r'\s',text) and re.search(r'[A-Za-z]',text):
  result=[];pending=''
  for word in text.split():
   if len(word)>limit:raise ValueError('单个单词过长，请使用发音词典或拆分')
   if pending and len(pending)+1+len(word)>limit:result.append(pending);pending=''
   pending=(pending+' '+word).strip()
  if pending:result.append(pending)
  return result
 result=[]
 while text:
  if len(text)<=limit:result.append(text);break
  end=max(text.rfind(mark,0,limit)+1 for mark in '。！？.!?；;')
  if end<limit//3:end=limit
  result.append(text[:end]);text=text[end:]
 return result

def fingerprint(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def asset_valid(asset):
 return isinstance(asset,dict) and isinstance(asset.get('path'),str) and Path(asset['path']).is_absolute() and Path(asset['path']).is_file() and digest(asset['path'])==asset.get('sha256')

def check_new_audio(path,expected,language,resources):
 import numpy as np,soundfile as sf
 from faster_whisper import WhisperModel
 from scipy.signal import resample_poly
 from math import gcd
 pcm,sr=sf.read(path,dtype='float32');d=gcd(sr,16000);model=WhisperModel(str(Path(resources)/'Models/asr-small'),device='cpu',compute_type='int8',cpu_threads=4,local_files_only=True)
 segments,_=model.transcribe(resample_poly(pcm,16000//d,sr//d).astype('float32'),language=LANGUAGES[language],beam_size=5,condition_on_previous_text=False,word_timestamps=True)
 segments=list(segments);recognized=' '.join(x.text.strip() for x in segments)
 def norm(s):
  s=unicodedata.normalize('NFKC',s).casefold().replace('’',"'")
  if language in ('chinese','japanese'):return ''.join(c for c in s if unicodedata.category(c)[0] in 'LN')
  return ' '.join(re.findall(r"[^\W_]+(?:'[^\W_]+)*",s,flags=re.UNICODE))
 finite=bool(np.isfinite(pcm).all());peak=float(np.max(np.abs(pcm)));exact=norm(expected)==norm(recognized)
 return dict(expected_spoken=expected,recognized=recognized,language=language,text_status='asr_exact_match' if exact else 'asr_discrepancy_unresolved',technical_pass=finite and 0.01<peak<1 and exact,spoken_error_confirmed=False,actual_listening_performed=False,initial_prompt=None,output_sha256=digest(path),word_alignment=[dict(text=w.word,start=w.start,end=w.end) for s in segments for w in (s.words or [])] if exact else [])

def generate_clips(project,path,engine,resources,data,emit,selected=None):
 import numpy as np,soundfile as sf,mlx.core as mx
 from mlx_audio.tts.utils import load_model
 from studio_release import split_script
 from finish import finish
 validate(project,engine)
 if selected is not None and not any(c['id']==selected for c in project['clips']):raise ValueError('未选中有效片段')
 model=None;previous=signal.getsignal(signal.SIGTERM)
 def cancel(*_):raise InterruptedError('已取消，已完成片段与旧录音保留')
 signal.signal(signal.SIGTERM,cancel)
 folder=Path(data)/'StudioProjects'/str(uuid.UUID(project['id']))/'Audio';folder.mkdir(parents=True,exist_ok=True)
 try:
  for c in project['clips']:
   if c.get('imported'):continue
   if selected is not None and c['id']!=selected:continue
   followers=sequence_followers(project,c);old_duration=effective_duration(c)
   wanted=binding(c,project);key=fingerprint(wanted)
   if selected is None and asset_valid(c.get('audio')) and c['audio'].get('binding')==key:
    c['status']='needs_listening';emit('progress',message='复用已保存片段：'+c.get('name',c['id']));continue
   take=wanted['take']+1 if selected is not None else wanted['take'];wanted['take']=take;key=fingerprint(wanted)
   voice,reference=approved_voice(engine,wanted['voice_id']);ref,sr=sf.read(reference,dtype='float32')
   if sr!=24000 or ref.ndim!=1:raise ValueError('已确认参考格式不符，未替换声音')
   if model is None:model=load_model(str(Path(data)/'Models/qwen3-voice-base'))
   emit('progress',message='本地录制：'+c.get('name',c['id'])+' · '+wanted['language'])
   chunks=[];mx.random.seed(voice['profile']['seed']+int(hashlib.sha256(c['id'].encode()).hexdigest()[:6],16)+100000*take)
   # Short bounded pieces work for both spaced and unspaced languages.
   pieces=speech_pieces(wanted['spoken'],600 if c.get('context_retake') else 220)
   for piece in pieces:
    for r in model.generate(piece,ref_audio=mx.array(ref),ref_text=voice['reference']['text'],lang_code=wanted['language'],temperature=.65,max_tokens=1800,verbose=False):
     if r.sample_rate!=24000:raise ValueError('模型输出采样率变化')
     chunks.append(np.asarray(r.audio,dtype='float32'))
   if not chunks:raise ValueError('片段未生成音频')
   pcm=np.concatenate(chunks)
   if pcm.ndim!=1 or not len(pcm) or not np.isfinite(pcm).all():raise ValueError('片段采样无效')
   peak=float(np.max(np.abs(pcm)))
   if peak>.95:pcm*=.95/peak
   output=folder/(str(uuid.uuid4())+'.wav');config=json.loads((Path(engine)/'voice-profile.json').read_text());config['version']='project-studio-v1';config['pacing'].update(tempo=wanted['speed'],preserve_paragraph_gaps=True)
   pcm,sr,processing=finish(pcm,24000,output,config,Path(resources))
   # A bounded whole-take gain preserves dynamics; never normalize each sentence.
   pcm,seam=match_retake_level(pcm,sr,c.get('retake_level_reference',c.get('audio')))
   processing['retake_level_match']=seam
   sf.write(output,pcm,sr,subtype='PCM_16')
   # Only new audio is recognized; reused/old recordings are never rechecked.
   review=check_new_audio(output,wanted['spoken'],wanted['language'],resources);write_json(output.with_suffix('.review.json'),review)
   asset=dict(path=str(output),sha256=digest(output),seconds=len(pcm)/sr,binding=key,request=wanted,review=review,processing=processing)
   if len(pcm)/sr<=c.get('trim_in',0)+c.get('trim_out',0):raise ValueError('新录音短于原裁剪范围，旧版本保留，请先调整裁剪')
   if c.get('audio'):c.setdefault('history',[]).append(c['audio'])
   c['audio']=asset;c['take']=take;c['status']='needs_listening';shift_followers(followers,effective_duration(c)-old_duration);mark_retake_overlap(project,c);project.pop('mixdown',None);project.pop('chapter_mixdown',None);write_json(path,project)
 finally:
  signal.signal(signal.SIGTERM,previous)
  if model is not None:del model;mx.clear_cache()
 write_json(path,project);return project

def match_retake_level(pcm,sr,old):
 import numpy as np,soundfile as sf
 evidence=dict(applied=False,actual_listening_performed=False)
 if not asset_valid(old):return pcm,evidence
 original,rate=sf.read(old['path'],dtype='float32')
 def active_rms(x):
  x=np.asarray(x);x=x.mean(axis=1) if x.ndim==2 else x
  peak=float(np.max(np.abs(x))) if x.size else 0
  active=x[np.abs(x)>max(.003,peak*.08)]
  return float(np.sqrt(np.mean(active**2))) if active.size else 0
 a,b=active_rms(original),active_rms(pcm)
 if min(a,b)<.005:return pcm,evidence
 db=float(np.clip(20*np.log10(a/b),-3,3));gain=min(10**(db/20),.95/max(float(np.max(np.abs(pcm))),1e-9))
 return pcm*gain,dict(applied=True,gain_db=float(20*np.log10(gain)),reference_sha256=old['sha256'],actual_listening_performed=False)

def context_retake(project,path,engine,resources,data,emit,clip_id,scope='both'):
 """Generate one continuous local passage, then atomically replace only that range."""
 import copy
 if scope not in ('previous','next','both'):raise ValueError('相邻重录范围无效')
 validate(project,engine);clips=project['clips']
 index=next((i for i,c in enumerate(clips) if c['id']==clip_id),None)
 if index is None or clips[index].get('imported'):raise ValueError('请选择配音片段')
 lo=max(0,index-1) if scope in ('previous','both') else index
 hi=min(len(clips)-1,index+1) if scope in ('next','both') else index
 group=clips[lo:hi+1]
 if len(group)<2:raise ValueError('此方向没有相邻片段，请选择另一范围')
 voice=lambda c:next(r['voice_id'] for r in project['roles'] if r['id']==c['role_id'])
 first=group[0]
 for c in group:
  if not asset_valid(c.get('audio')):raise ValueError('相邻重录需各句已有完整录音；请先生成缺失片段，原工程保留')
  if c.get('imported') or (voice(c),c['language'],c.get('speed',1),c.get('gain_db',0),c.get('chapter_id'))!=(voice(first),first['language'],first.get('speed',1),first.get('gain_db',0),first.get('chapter_id')):raise ValueError('相邻重录需同声音、语言、语速、音量和章节；原工程未改动')
  if c.get('trim_in',0) or c.get('trim_out',0):raise ValueError('相邻片段有裁剪，请先确认完整原稿再重录；原录音保留')
 for a,b in zip(group,group[1:]):
  gap=b.get('start',0)-a.get('start',0)-effective_duration(a)
  if b.get('timing_manual') or not -.001<=gap<=1:raise ValueError('相邻片段不是连续旁白，不能自动合并重录')
 text=' '.join(c['text'].strip() for c in group)
 spoken=spoken_text(text,first['language'],project.get('dictionary',[]))
 if len(spoken)>600:raise ValueError('连贯重录最多600字符，请缩小相邻范围')
 candidate=copy.deepcopy(first);candidate.update(text=text,name='连贯重录 · '+clips[index].get('name','片段'),take=max(c.get('take',0) for c in group),context_retake=dict(scope=scope,source_clips=copy.deepcopy(group),actual_listening_performed=False))
 candidate['history']=[copy.deepcopy(asset) for c in group for asset in c.get('history',[])+([c['audio']] if c.get('audio') else [])]
 candidate.pop('audio',None);candidate.pop('sentence_parent_id',None)
 candidate['retake_level_reference']=copy.deepcopy(first['audio'])
 staged=copy.deepcopy(project);staged['clips']=[candidate]
 temporary=Path(path).with_name('context-staging-'+str(uuid.uuid4())+'.json')
 original_preview=temporary.with_suffix('.previous.json')
 try:
  previous=copy.deepcopy(project);previous['clips']=copy.deepcopy(group)
  for c in previous['clips']:c['start']=c.get('start',0)-first.get('start',0)
  # This is playback of the old take, not recognition of edited text.
  mix(previous,original_preview,engine,data,require_fresh=False)
  candidate['history'].append(previous['mixdown'])
  generate_clips(staged,temporary,engine,resources,data,emit,candidate['id'])
  staged['clips'][0].pop('retake_level_reference',None)
  result=copy.deepcopy(project);replacement=staged['clips'][0];old_end=group[-1].get('start',0)+effective_duration(group[-1])
  followers=sequence_followers(result,result['clips'][hi])
  shift_followers(followers,replacement.get('start',0)+effective_duration(replacement)-old_end)
  result['clips'][lo:hi+1]=[replacement];result.pop('mixdown',None);result.pop('chapter_mixdown',None);result.pop('clip_preview',None)
  validate(result,engine);mark_retake_overlap(result,replacement);write_json(path,result)
  project.clear();project.update(result);return project
 finally:
  temporary.unlink(missing_ok=True)
  original_preview.unlink(missing_ok=True)

def timeline(project,engine,require_fresh=True):
 validate(project,engine);items=[]
 for c in project['clips']:
  asset=c.get('audio')
  if not asset_valid(asset):raise ValueError('片段音频缺失或已改变：'+c.get('name',c['id']))
  if not c.get('imported') and require_fresh and asset.get('binding')!=fingerprint(binding(c,project)):raise ValueError('片段设置已修改，请先生成：'+c.get('name',c['id']))
  duration=number(asset.get('seconds'),0.001,3600,'片段时长');lo=c.get('trim_in',0);hi=c.get('trim_out',0)
  if lo+hi>=duration:raise ValueError('裁剪不能删除整个片段')
  end=c.get('start',0)+duration-lo-hi
  if end>3600:raise ValueError('混音最多一小时')
  items.append(dict(clip=c,start=c.get('start',0),duration=duration-lo-hi,end=end))
 for i,a in enumerate(items):
  for b in items[i+1:]:
   if min(a['end'],b['end'])-max(a['start'],b['start'])>.001 and not any(x['clip'].get('imported') for x in (a,b)) and (any(x['clip'].get('timing_review_required') for x in (a,b)) or not any(x['clip'].get('timing_manual') for x in (a,b))):raise ValueError('配音片段重叠，请按顺序排列；如需主动叠音，请手动定位片段确认')
 if not items:raise ValueError('时间线没有片段')
 return items

def mix(project,path,engine,data,require_fresh=True):
 import numpy as np,soundfile as sf
 from scipy.signal import resample_poly
 from math import gcd
 items=timeline(project,engine,require_fresh=require_fresh)
 if sum(x['duration'] for x in items)>3600:raise ValueError('片段有效总时长最多一小时')
 sr=24000;tracks=[];length=0
 for item in items:
  c=item['clip'];info=sf.info(c['audio']['path']);rate=info.samplerate
  if info.channels>2 or not 8000<=rate<=192000:raise ValueError('只支持有效的单声道或立体声音频')
  actual=info.frames/rate
  if abs(actual-c['audio']['seconds'])>.03:raise ValueError('片段实际时长与记录不一致')
  lo=round(c.get('trim_in',0)*rate);stop=info.frames-round(c.get('trim_out',0)*rate)
  audio,_=sf.read(c['audio']['path'],start=lo,stop=stop,dtype='float32',always_2d=True)
  if not np.isfinite(audio).all():raise ValueError('音频包含无效采样')
  if rate!=sr:
   divisor=gcd(rate,sr);audio=resample_poly(audio,sr//divisor,rate//divisor,axis=0).astype('float32')
  if audio.shape[1]==1:audio=np.repeat(audio,2,axis=1)
  audio*=10**(c.get('gain_db',0)/20);fade=min(round(.008*sr),len(audio)//2)
  if fade:audio[:fade]*=np.linspace(0,1,fade)[:,None];audio[-fade:]*=np.linspace(1,0,fade)[:,None]
  start=round(item['start']*sr);tracks.append((start,audio));length=max(length,start+len(audio))
 target=Path(data)/'StudioProjects'/str(uuid.UUID(project['id']))/'Mixes';target.mkdir(parents=True,exist_ok=True);output=target/(str(uuid.uuid4())+'.wav');temporary=output.with_suffix('.float.wav');peak=0.0
 try:
  with sf.SoundFile(temporary,'w',samplerate=sr,channels=2,subtype='FLOAT') as f:
   for start in range(0,length,8192):
    block=np.zeros((min(8192,length-start),2),dtype='float32')
    for offset,audio in tracks:
     lo=max(start,offset);hi=min(start+len(block),offset+len(audio))
     if hi>lo:block[lo-start:hi-start]+=audio[lo-offset:hi-offset]
    if not np.isfinite(block).all():raise ValueError('混音采样无效')
    peak=max(peak,float(np.max(np.abs(block))));f.write(block)
  scale=min(1,.98/peak) if peak else 1
  with sf.SoundFile(temporary) as src,sf.SoundFile(output,'w',samplerate=sr,channels=2,subtype='PCM_16') as out:
   for block in src.blocks(blocksize=8192,dtype='float32',always_2d=True):out.write(block*scale)
  asset=dict(path=str(output),sha256=digest(output),seconds=length/sr,peak_before=peak,scale=scale,sample_rate=sr,actual_listening_performed=False)
  project['mixdown']=asset;write_json(path,project);return project
 finally:
  if temporary.exists():temporary.unlink()

def _run_project(request,engine,resources,data,emit):
 source=Path(request['project_path'])
 if not source.is_absolute() or source.stat().st_size>2*1024*1024:raise ValueError('项目文件位置或大小无效')
 project=validate(json.loads(source.read_text()),engine);uuid.UUID(project['id']);action=request['action']
 if action=='export_bundle':
  export_bundle(project,Path(request['destination']),engine);emit('progress',message='完整工程包已保存：'+request['destination'])
 elif action=='import_bundle':
  import_path=Path(data)/'BundleImports'/(str(uuid.uuid4())+'.json');import_path.parent.mkdir(parents=True,exist_ok=True);project=import_bundle(Path(request['bundle_path']),import_path,engine,data)
 elif action=='split_sentences':
  split_sentences(project,request.get('clip_id'),engine);write_json(source,project)
 elif action=='preview_clip':
  import copy
  selected=copy.deepcopy(project);selected['clips']=[c for c in selected['clips'] if c['id']==request.get('clip_id')]
  if not selected['clips']:raise ValueError('请选择有效片段')
  selected['clips'][0]['start']=0;mix(selected,Path(data)/('preview-'+str(uuid.uuid4())+'.json'),engine,data);project['clip_preview']=selected['mixdown'];write_json(source,project)
 elif action=='mix_chapter':
  selected=chapter_project(project,request.get('chapter_id'));mix(selected,Path(data)/('chapter-'+str(uuid.uuid4())+'.json'),engine,data);selected['mixdown']['chapter_id']=request['chapter_id'];project['chapter_mixdown']=selected['mixdown'];write_json(source,project)
 elif action=='retake' and (not isinstance(request.get('clip_id'),str) or not request['clip_id']):raise ValueError('请选择要重录的单个片段')
 if action=='retake_context':context_retake(project,source,engine,resources,data,emit,request.get('clip_id'),request.get('scope','both'))
 elif action in ('generate_missing','retake'):generate_clips(project,source,engine,resources,data,emit,request.get('clip_id') if action=='retake' else None)
 elif action=='mix':mix(project,source,engine,data)
 elif action not in ('export_bundle','import_bundle','split_sentences','mix_chapter','preview_clip'):raise ValueError('未知项目操作')
 emit('studio_project',project=project,action=action)


def validate_chapters(project):
 chapters=project.get('chapters',[])
 if not isinstance(chapters,list) or len(chapters)>100:raise ValueError('章节最多100个')
 ids=set()
 for chapter in chapters:
  if not isinstance(chapter,dict) or not isinstance(chapter.get('id'),str) or not chapter['id'] or chapter['id'] in ids or not isinstance(chapter.get('name'),str) or not chapter['name'].strip() or len(chapter['name'])>120:raise ValueError('章节名称或编号无效')
  ids.add(chapter['id'])
 for c in project['clips']:
  if c.get('chapter_id') and c['chapter_id'] not in ids:raise ValueError('片段章节不存在')

def sentence_texts(text):
 # Keep every original character, including whitespace, punctuation and closing quotes.
 # Avoid splitting decimal numbers, initials, and common English titles.
 boundaries=[]
 for match in re.finditer(r'[.!?。！？]+["\'”’」』）)]*(?:\s+|(?=[^\x00-\x7f])|$)',text):
  end=match.end();prefix=text[:match.start()+1]
  if match.group().startswith('.') and (re.search(r'(?i)\b(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|etc)\.$',prefix) or re.search(r'\b[A-Z]\.$',prefix)):continue
  boundaries.append(end)
 # CJK full stops need not be followed by a space or a non-ASCII character.
 for match in re.finditer(r'[。！？]+["\'”’」』）)]*\s*',text):boundaries.append(match.end())
 result=[];cursor=0
 for end in sorted(set(boundaries)):
  if end>cursor and text[cursor:end].strip():result.append(text[cursor:end]);cursor=end
 if text[cursor:].strip():result.append(text[cursor:])
 elif result:result[-1]+=text[cursor:]
 return result or [text]

def split_sentences(project,clip_id,engine):
 import copy
 validate(project,engine)
 original=next((c for c in project['clips'] if c['id']==clip_id),None)
 if original is None or original.get('imported'):raise ValueError('请选择配音文稿片段')
 followers=sequence_followers(project,original);old_duration=effective_duration(original)
 parts=sentence_texts(original['text'])
 if len(parts)<2:raise ValueError('所选片段只有一句，无需拆分；可直接重录')
 if len(project['clips'])+len(parts)-1>100:raise ValueError('拆分后超过100片段，原稿未修改')
 retained=split_existing_audio(original,parts,project) if original.get('audio') else None
 cs=[];cursor=original.get('start',0)
 for i,text in enumerate(parts):
  c=copy.deepcopy(original);c.update(id=str(uuid.uuid4()),name=original.get('name','片段')+' · 句'+str(i+1),text=text,start=cursor,trim_in=0,trim_out=0,take=0,status='needs_generation',sentence_parent_id=original['id'])
  c.pop('audio',None);c.pop('history',None)
  if i==0:
   c['history']=copy.deepcopy(original.get('history',[]))
   if original.get('audio'):c['history'].append(copy.deepcopy(original['audio']))
  c['original_sentence_source']=dict(clip_id=original['id'],text=original['text'])
  if i>0:c.pop('timing_manual',None)
  if retained:
   asset,offset=retained[i];c.update(start=original.get('start',0)+offset,status='needs_listening',audio=asset)
   c['audio']['binding']=fingerprint(binding(c,project))
  cs.append(c);cursor+=3.3
 if not retained:shift_followers(followers,(cursor-.3-original.get('start',0))-old_duration)
 position=project['clips'].index(original);project['clips'][position:position+1]=cs;project.pop('mixdown',None);project.pop('chapter_mixdown',None)
 validate(project,engine);return project

def split_existing_audio(original,parts,project):
 """Cut only at verified quiet gaps. Never re-recognize an old recording."""
 import numpy as np,soundfile as sf
 asset=original['audio'];review=asset.get('review',{})
 if not asset_valid(asset) or review.get('output_sha256')!=asset['sha256'] or not review.get('word_alignment') or original.get('trim_in',0) or original.get('trim_out',0):raise ValueError('原录音缺少安全逐词定位或已有裁剪，未拆分也未重识别；请保留整段或使用已有句子片段')
 # Stale text cannot be paired with the old waveform.
 if asset.get('binding')!=fingerprint(binding(original,project)):raise ValueError('文稿已修改，不能把旧录音当作新文稿拆分；原声保留')
 if original['language'] in ('japanese','korean'):raise ValueError('此语言暂不支持无损句子定位；保留原录音，不猜测切点')
 chinese=original['language']=='chinese'
 tokens=lambda s:re.findall(r"[a-z0-9]+(?:'[^\W_]+)*|[^\W_]" if chinese else r"[^\W_]+(?:'[^\W_]+)*",unicodedata.normalize('NFKC',s).casefold().replace('’',"'"))
 words=review['word_alignment'];spoken=[spoken_text(t,original['language'],project.get('dictionary',[])) for t in parts]
 if (not chinese and any(len(tokens(w['text']))!=1 for w in words)) or sum([tokens(w['text']) for w in words],[])!=sum([tokens(t) for t in spoken],[]):raise ValueError('逐词定位与句子不能一一对应，原录音未拆分')
 word_ends=[];total=0
 for w in words:total+=len(tokens(w['text']));word_ends.append(total)
 pcm,sr=sf.read(asset['path'],dtype='float32',always_2d=True);cuts=[0];count=0
 for text in spoken[:-1]:
  count+=len(tokens(text))
  if count not in word_ends:raise ValueError('句界位于识别词内部，保留整段，不猜测中文切点')
  boundary=word_ends.index(count);a,b=words[boundary],words[boundary+1]
  lo=max(cuts[-1]+1,round((a['end']-.06)*sr));hi=min(len(pcm),round((b['start']+.06)*sr))
  hop=max(1,round(.01*sr));quiet=[];threshold=max(.002,float(np.max(np.abs(pcm)))*.025)
  for i in range(lo,hi-hop+1,hop):
   if float(np.sqrt(np.mean(pcm[i:i+hop]**2)))<threshold:quiet.append(i)
  runs=[]
  for i in quiet:
   if runs and i==runs[-1][-1]+hop:runs[-1].append(i)
   else:runs.append([i])
  runs=[r for r in runs if len(r)>=4]
  if not runs:raise ValueError('句间没有可靠静音切点，未切断语音；请使用整段重录')
  run=max(runs,key=len);cuts.append(run[len(run)//2])
 cuts.append(len(pcm));result=[]
 for lo,hi in zip(cuts,cuts[1:]):
  output=Path(asset['path']).with_name('sentence-'+str(uuid.uuid4())+'.wav');sf.write(output,pcm[lo:hi],sr,subtype='PCM_16')
  result.append((dict(path=str(output),sha256=digest(output),seconds=(hi-lo)/sr,source_audio_sha256=asset['sha256'],source_frames=[lo,hi],actual_listening_performed=False),lo/sr))
 return result

def chapter_project(project,chapter_id):
 import copy
 validate_chapters(project)
 if chapter_id not in {x['id'] for x in project.get('chapters',[])}:raise ValueError('请选择有效章节')
 selected=copy.deepcopy(project);selected['clips']=[c for c in selected['clips'] if c.get('chapter_id')==chapter_id]
 if not selected['clips']:raise ValueError('所选章节没有片段')
 offset=min(c.get('start',0) for c in selected['clips'])
 for c in selected['clips']:c['start']=c.get('start',0)-offset
 selected.pop('mixdown',None);return selected

def audio_assets(project):
 for c in project['clips']:
  if c.get('audio'):yield c['audio']
  for item in c.get('history',[]):yield item
 for key in ('mixdown','chapter_mixdown'):
  if project.get(key):yield project[key]

def export_bundle(project,destination,engine):
 import copy,zipfile,tempfile
 validate(project,engine);destination=Path(destination)
 if destination.exists():raise ValueError('完整工程包目标已存在，请另存新名称')
 if not destination.is_absolute() or destination.suffix!='.voicebundle':raise ValueError('请选择.voicebundle工程包位置')
 portable=copy.deepcopy(project);portable.pop('clip_preview',None);files={}
 for asset in audio_assets(portable):
  if not asset_valid(asset):raise ValueError('音频缺失或校验失败，未生成不完整工程包')
  key=asset['sha256'];files[key]=Path(asset['path']);asset['path']='Audio/'+key+'.wav'
 payload=json.dumps(portable,ensure_ascii=False,indent=2).encode()
 if len(files)>1000 or len(payload)>2*1024**2:raise ValueError('工程历史过大，无法打包；原项目未改变')
 manifest={'format':'offline-voice-bundle-v1','project_sha256':hashlib.sha256(payload).hexdigest(),'audio':{k:dict(path='Audio/'+k+'.wav',bytes=v.stat().st_size) for k,v in files.items()},'includes_models':False,'includes_voice_references':False}
 if sum(x.stat().st_size for x in files.values())>4*1024**3:raise ValueError('完整工程包音频最多4GB')
 destination.parent.mkdir(parents=True,exist_ok=True)
 fd,temp=tempfile.mkstemp(dir=destination.parent,suffix='.partial');os.close(fd)
 try:
  with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
   z.writestr('project.json',payload);z.writestr('manifest.json',json.dumps(manifest))
   for key,source in files.items():z.write(source,'Audio/'+key+'.wav')
  # Exclusive publish, never replace a source or an existing user file.
  os.link(temp,destination)
 finally:Path(temp).unlink(missing_ok=True)
 return manifest

def import_bundle(bundle,project_path,engine,data):
 import zipfile,tempfile,shutil,copy
 bundle=Path(bundle);project_path=Path(project_path)
 if not zipfile.is_zipfile(bundle):raise ValueError('无法打开工程包：文件损坏或格式不正确，原工程保留')
 root=Path(data)/'StudioProjects';root.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(bundle) as z:
  names=z.namelist()
  if len(names)!=len(set(names)) or len(names)>1005 or any(n.startswith('/') or '..' in Path(n).parts or '\\' in n for n in names):raise ValueError('工程包路径无效')
  infos={i.filename:i for i in z.infolist()}
  if any((i.external_attr>>16)&0o170000==0o120000 for i in infos.values()) or sum(i.file_size for i in infos.values())>4*1024**3+4*1024**2:raise ValueError('工程包过大或含链接')
  if any(n not in infos or infos[n].file_size>2*1024**2 for n in ['project.json','manifest.json']):raise ValueError('工程包描述文件无效')
  payload=z.read('project.json');manifest=json.loads(z.read('manifest.json'))
  if manifest.get('format')!='offline-voice-bundle-v1' or hashlib.sha256(payload).hexdigest()!=manifest.get('project_sha256'):raise ValueError('工程包文稿校验失败')
  project=validate(json.loads(payload),engine);assets=list(audio_assets(project));expected={'project.json','manifest.json'};declared=manifest.get('audio',{})
  for a in assets:
   key=a.get('sha256','');name='Audio/'+key+'.wav'
   if not re.fullmatch('[0-9a-f]{64}',key) or a.get('path')!=name or name not in infos or not isinstance(declared.get(key),dict) or declared[key].get('bytes')!=infos[name].file_size:raise ValueError('工程包音频清单无效')
   expected.add(name)
  if set(names)!=expected or set(declared)!={a['sha256'] for a in assets}:raise ValueError('工程包有未声明素材')
  new_id=str(uuid.uuid4());target=root/new_id;stage=Path(tempfile.mkdtemp(prefix='.import-',dir=root))
  try:
   for name in expected-{'project.json','manifest.json'}:
    output=stage/name;output.parent.mkdir(exist_ok=True)
    with z.open(name) as src,output.open('xb') as out:shutil.copyfileobj(src,out,1024*1024)
    if digest(output)!=Path(name).stem:raise ValueError('工程包音频校验失败')
    import soundfile as sf
    info=sf.info(output)
    if not 1<=info.channels<=2 or not 8000<=info.samplerate<=192000 or not 0<info.duration<=3600:raise ValueError('工程包音频格式无效')
    if any(abs(a.get('seconds',0)-info.duration)>.03 for a in assets if a['path']==name):raise ValueError('工程包音频时长与清单不一致')
   for a in assets:a['path']=str(target/a['path'])
   project['id']=new_id;project['imported_from_project_id']=json.loads(payload)['id'];write_json(stage/'project.json',project);stage.rename(target)
   try:write_json(project_path,project)
   except Exception:shutil.rmtree(target);raise
  finally:
   if stage.exists():shutil.rmtree(stage)
 return project


def run(request,engine,resources,data,emit):
 previous=signal.getsignal(signal.SIGTERM)
 def cancel(*_):raise InterruptedError('已取消，原工程及原始音频保留')
 signal.signal(signal.SIGTERM,cancel)
 try:return _run_project(request,engine,resources,data,emit)
 finally:signal.signal(signal.SIGTERM,previous)


def effective_duration(c):
 return max(.1,c.get('audio',{}).get('seconds',3)-c.get('trim_in',0)-c.get('trim_out',0))

def sequence_followers(project,c):
 # Follow only forward, non-overlapping array order; explicit manual positioning breaks the chain.
 clips=project['clips'];index=clips.index(c);end=c.get('start',0)+effective_duration(c);result=[]
 for following in clips[index+1:]:
  if following.get('timing_manual') or following.get('start',0)<end-.001 or following.get('start',0)-end>1:break
  result.append(following);end=following.get('start',0)+effective_duration(following)
 return result

def shift_followers(followers,delta):
 positions=[c.get('start',0)+delta for c in followers]
 if any(x<0 or x+effective_duration(c)>3600 for c,x in zip(followers,positions)):raise ValueError('调整后超出一小时时间线，原工程保留')
 for c,value in zip(followers,positions):c['start']=value


def mark_retake_overlap(project,c):
 c.pop('timing_review_required',None)
 lo=c.get('start',0);hi=lo+effective_duration(c)
 if any(other is not c and not other.get('imported') and min(hi,other.get('start',0)+effective_duration(other))-max(lo,other.get('start',0))>.001 for other in project['clips']):c['timing_review_required']=True
