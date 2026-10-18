"""User-approved identities and durable narration jobs. No design or cloud path."""
import hashlib,json,re,uuid,shutil,difflib,os,time
import signal as process_signals
from pathlib import Path

def digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()

def write_json(path,value):
 path=Path(path);tmp=path.with_suffix('.writing')
 with tmp.open('w') as f:
  json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
 tmp.replace(path)

def read_cache_json(path):
 try:return json.loads(Path(path).read_text())
 except (OSError,ValueError):return None

def completed_result(state,binding):
 if state.get('status') not in ('complete','needs_review'):return None
 output=Path(state.get('output',''))
 meta=read_cache_json(output.with_suffix('.json')) if output.is_file() else None
 check=read_cache_json(output.parent/'technical-review.json') if meta else None
 if not meta or not check or any(meta.get(k)!=v for k,v in binding.items()) or digest(output)!=meta.get('output_sha256') or check.get('expected')!=binding['text'] or meta.get('technical_pass')!=check.get('technical_pass'):
  raise ValueError('已完成录音或技术证据不完整；原文件已保留，不能自动重做或认定合格')
 if state.get('review') and (digest(output.parent/'technical-review.json')!=state['review'].get('sha256') or state['review'].get('output_sha256')!=meta['output_sha256']):raise ValueError('已保存的技术检查证据校验失败；不自动重新识别或放行')
 return meta

def archive_previous_error(state):
 previous=state.pop('last_error',None)
 if previous is None:return False
 state.setdefault('error_history',[]).append(dict(error=previous,previous_status=state.get('status'),archived_at=time.time()))
 return True

def split_script(text,limit=70):
 """Preserve every word; sentence boundaries preferred, bounded fallback for run-ons."""
 if re.search(r'[\u3400-\u9fff]',text):return split_chinese(text)
 result=[]
 for paragraph in re.split(r'\n\s*\n|\n',text.strip()):
  if not paragraph.strip():continue
  sentences=re.split(r'(?<=[.!?])\s+(?=[A-Z\d"“])',paragraph.strip())
  pending=[]
  for sentence in sentences:
   words=sentence.split()
   if pending and len(pending)+len(words)>limit:
    result.append({'text':' '.join(pending),'paragraph_end':False});pending=[]
   while len(words)>limit:
    if pending:result.append({'text':' '.join(pending),'paragraph_end':False});pending=[]
    result.append({'text':' '.join(words[:limit]),'paragraph_end':False});words=words[limit:]
   pending.extend(words)
  if pending:result.append({'text':' '.join(pending),'paragraph_end':True})
  else:result[-1]['paragraph_end']=True
 return result

def split_chinese(text,limit=180):
 result=[]
 for paragraph in re.split(r'\n+',text.strip()):
  remaining=paragraph.strip()
  while remaining:
   end=min(limit,len(remaining))
   if len(remaining)>limit:
    marks=list(re.finditer(r'[。！？；，.!?;]',remaining[:limit]))
    if marks and marks[-1].end()>=limit//3:end=marks[-1].end()
    else:
     while end>0 and remaining[end-1].isascii() and remaining[end-1].isalnum() and remaining[end].isascii() and remaining[end].isalnum():end-=1
     if end==0:raise ValueError('中英文混排中存在过长的连续英文词，请先拆分')
   result.append(dict(text=remaining[:end],paragraph_end=end==len(remaining)));remaining=remaining[end:]
 return result

def text_repair_segments(segments,recognized,limit=3):
 expected=[];owners=[]
 chinese=any(re.search(r'[\u3400-\u9fff]',s['text']) for s in segments)
 def tokens(s):return re.findall(r'[a-z0-9]+|[\u3400-\u9fff]',s.lower()) if chinese else re.findall(r'[a-z0-9]+',s.lower())
 for i,segment in enumerate(segments):
  words=tokens(segment['text']);expected.extend(words);owners.extend([i]*len(words))
 actual=tokens(recognized);bad=set()
 for kind,a,b,c,d in difflib.SequenceMatcher(None,expected,actual,autojunk=False).get_opcodes():
  if kind=='equal':continue
  if a==b:bad.add(owners[min(a,len(owners)-1)])
  else:bad.update(owners[a:b])
 return sorted(bad) if bad and (limit is None or len(bad)<=limit) else []

def catalog(engine):
 p=Path(engine)/'approved-voices.json'
 return json.loads(p.read_text()) if p.exists() else None

def approved_voice(engine,key):
 entry=next((v for v in catalog(engine)['voices'] if v['id']==key),None)
 if entry is None:raise ValueError('本版仅使用用户已确认声音；新增音色已暂停')
 reference=Path(engine)/entry['reference']['path']
 if digest(reference)!=entry['reference']['sha256']:raise ValueError('已确认参考录音校验失败，不能替换声音')
 return entry,reference

def generate(request,engine,resources,data,emit):
 import os
 for key in ['HF_TOKEN','HUGGING_FACE_HUB_TOKEN','OPENAI_API_KEY','ELEVENLABS_API_KEY']:os.environ.pop(key,None)
 os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1',TOKENIZERS_PARALLELISM='false')
 text=request.get('text','').strip();speed=float(request.get('speed',1));gap=float(request.get('paragraph_gap',.6))
 if not 1<=len(text)<=12000:raise ValueError('请输入1至12000个字符的中文或英文文稿')
 language='Chinese' if re.search(r'[\u3400-\u9fff]',text) else 'English'
 if not .9<=speed<=1.2 or not .0<=gap<=2:raise ValueError('语速或停顿超出范围')
 voice,reference=approved_voice(engine,request.get('voice_id',''));segments=split_script(text)
 takes={int(k):int(v) for k,v in request.get('segment_takes',{}).items()}
 if any(i<0 or i>=len(segments) or t<0 or t>2 for i,t in takes.items()):raise ValueError('分段重录参数无效')
 binding=dict(text=text,voice_id=voice['id'],profile_hash=voice['profile_hash'],reference_sha256=voice['reference']['sha256'],speed=speed,paragraph_gap=gap,pipeline='approved-four-v1')
 if language!='English':binding['language']=language
 if takes:binding['segment_takes']={str(i):t for i,t in takes.items()}
 key=hashlib.sha256(json.dumps(binding,sort_keys=True).encode()).hexdigest();folder=Path(data)/'NarrationJobs'/key;folder.mkdir(parents=True,exist_ok=True)
 statepath=folder/'job.json';state=json.loads(statepath.read_text()) if statepath.exists() else dict(binding=binding,segments=segments,completed={},status='pending')
 if state['binding']!=binding or state['segments']!=segments:raise ValueError('恢复任务与文稿不一致')
 existing=completed_result(state,binding)
 if existing is not None:
  corrected=archive_previous_error(state)
  if state.get('phase')!='exported':
   state['legacy_phase']=state.get('phase');state['phase']='exported';corrected=True
  if corrected:write_json(statepath,state)
  emit('complete',path=state['output'],seconds=existing['seconds'],voice_id=voice['id'],technical_pass=existing['technical_pass'],text_status=existing.get('text_status','asr_discrepancy_unresolved'),reused_completed=True)
  emit('progress',message='已恢复保存的录音及原检查结果；待复核状态保留，不重新识别。' if not existing['technical_pass'] else '已恢复保存的录音及技术检查；听感状态不变。')
  return existing
 archive_previous_error(state);state['attempt_count']=state.get('attempt_count',0)+1
 (folder/'script.txt').write_text(text);state['status']='pending';write_json(statepath,state)
 import numpy as np,soundfile as sf,mlx.core as mx
 from mlx_audio.tts.utils import load_model
 from finish import finish
 modelpath=Path(data)/'Models/qwen3-voice-base'
 state['status']='running';write_json(statepath,state)
 model=None;parts=[];rate=24000
 old_term=process_signals.getsignal(process_signals.SIGTERM)
 def interrupted(signum,frame):raise InterruptedError('用户取消，已完成分段可恢复')
 process_signals.signal(process_signals.SIGTERM,interrupted)
 try:
  rendered=state.get('render')
  if rendered:
   output=Path(rendered['path'])
   if not output.is_file() or digest(output)!=rendered['sha256']:raise ValueError('恢复用成片校验失败；保留原始分段，不自动覆盖损坏文件')
   target=output.parent;processing=rendered['processing'];render_seconds=rendered['seconds']
   emit('progress',message='恢复已完成的语速与停顿处理，不重复渲染…')
  else:
   state['phase']='recording';write_json(statepath,state)
   ref,ref_rate=sf.read(reference,dtype='float32')
   if ref_rate!=24000 or ref.ndim!=1:raise ValueError('参考录音格式不匹配，不能静默变换已确认声音')
   for i,segment in enumerate(segments):
    path=folder/f'part-{i:04d}.wav';saved=state['completed'].get(str(i))
    actual_seed=voice['profile']['seed']+i+100000*takes.get(i,0)
    raw_binding=dict(voice=voice['id'],profile=voice['profile_hash'],reference=voice['reference']['sha256'],text=segment['text'],seed=actual_seed,generation='approved-four-raw-v1')
    if language!='English':raw_binding['language']=language
    cache_key=hashlib.sha256(json.dumps(raw_binding,sort_keys=True).encode()).hexdigest()
    cache=Path(data)/'NarrationCache'/cache_key;cache.mkdir(parents=True,exist_ok=True)
    local_valid=bool(saved and path.is_file() and digest(path)==saved.get('sha256'))
    if not local_valid and (cache/'part.json').is_file() and (cache/'part.wav').is_file():
     candidate=read_cache_json(cache/'part.json')
     if isinstance(candidate,dict) and digest(cache/'part.wav')==candidate.get('sha256'):
      shutil.copy2(cache/'part.wav',path);saved=candidate;state['completed'][str(i)]=saved;write_json(statepath,state)
    if saved and path.is_file() and digest(path)==saved['sha256']:
     pcm,sr=sf.read(path,dtype='float32');emit('progress',message=f'恢复已完成段落 {i+1}/{len(segments)}…')
    else:
     if model is None:
      if not (modelpath/'model.safetensors').is_file():raise ValueError('请先导入 VoiceModel-Identity.zip 固定声音模型包')
      emit('progress',message='正在加载已确认声音…');model=load_model(str(modelpath))
     mx.random.seed(actual_seed);chunks=[];sr=24000
     emit('progress',message=f'本地录制 {i+1}/{len(segments)}…')
     for r in model.generate(segment['text'],ref_audio=mx.array(ref),ref_text=voice['reference']['text'],lang_code=language,temperature=.65,max_tokens=1800,verbose=False):chunks.append(np.asarray(r.audio,dtype='float32'));sr=r.sample_rate
     if not chunks:raise ValueError(f'第{i+1}段没有生成音频')
     pcm=np.concatenate(chunks)
     if sr!=24000 or pcm.ndim!=1 or not len(pcm) or not np.isfinite(pcm).all():raise ValueError(f'第{i+1}段音频无效')
     if np.max(np.abs(pcm))>.95:pcm=pcm*(.95/np.max(np.abs(pcm)))
     tmp=path.with_suffix('.writing.wav');sf.write(tmp,pcm,sr,subtype='PCM_16');tmp.replace(path)
     state['completed'][str(i)]=dict(sha256=digest(path),sample_rate=sr);write_json(statepath,state)
     cached=cache/'part.writing.wav';shutil.copy2(path,cached);cached.replace(cache/'part.wav');write_json(cache/'part.json',state['completed'][str(i)])
    if sr!=rate:raise ValueError('分段采样率不一致')
    parts.append(pcm)
    if i<len(segments)-1:parts.append(np.zeros(round(rate*(gap if segment['paragraph_end'] else .15)*speed),dtype='float32'))
   if model is not None:del model;model=None;mx.clear_cache()
   target=Path(data)/'Recordings'/str(uuid.uuid4());target.mkdir(parents=True);output=target/'narration.wav';(target/'script.txt').write_text(text)
   config=json.loads((Path(engine)/'voice-profile.json').read_text());config['version']=voice['id']+'-approved-v1';config['pacing']['tempo']=speed;config['pacing']['preserve_paragraph_gaps']=True
   state['phase']='rendering';write_json(statepath,state)
   emit('progress',message='正在处理语速并检查完整文稿…')
   signal,rate,processing=finish(np.concatenate(parts),rate,output,config,Path(resources))
   staged=output.with_suffix('.writing.wav');sf.write(staged,signal,rate,subtype='PCM_16');staged.replace(output)
   state['render']=dict(path=str(output),sha256=digest(output),seconds=len(signal)/rate,processing=processing);state['phase']='text_check';write_json(statepath,state)
   render_seconds=state['render']['seconds']
  from audition_qa import QualityCheck
  review=state.get('review');review_path=target/'technical-review.json'
  if review and review_path.is_file() and digest(review_path)==review.get('sha256') and review.get('output_sha256')==digest(output):
   check=json.loads(review_path.read_text());emit('progress',message='恢复已保存的全文检查结果，不重复识别…')
  else:
   check=QualityCheck(resources).sample(output,text,progress=lambda done,total:emit('progress',message=f'全文检查 {done/total:.0%}…'));write_json(review_path,check)
   state['review']=dict(sha256=digest(review_path),output_sha256=digest(output));write_json(statepath,state)
  state['phase']='finalizing';write_json(statepath,state)
  meta=dict(binding,seconds=render_seconds,processing=processing,technical_pass=check['technical_pass'],text_status=check['text_status'],spoken_error_confirmed=False,actual_listening_performed=False,voice_identity_user_confirmed=True,output_sha256=digest(output),offline=True,job_id=key)
  write_json(output.with_suffix('.json'),meta);state.update(status='complete' if check['technical_pass'] else 'needs_review',output=str(output),phase='exported');write_json(statepath,state)
  # An ASR mismatch is an unresolved observation, not proof of a spoken error.
  # Preserve source and output; never silently regenerate on a recognizer's vote.
  if not check['technical_pass']:
   bad=text_repair_segments(segments,check['recognized'],limit=None)
   comparisons=[]
   for index in bad:
    part=folder/f'part-{index:04d}.wav'
    comparisons.append(dict(index=index,raw_path=str(part),raw_sha256=digest(part),raw_asr_exact_match=None,raw_recognition_not_repeated=True,spoken_error_confirmed=False))
   write_json(target/'text-discrepancy-review.json',dict(status='unresolved_asr_discrepancy',output_sha256=meta['output_sha256'],segments=comparisons,automatic_retake=False,actual_listening_performed=False,spoken_error_confirmed=False))
  emit('complete',path=str(output),seconds=meta['seconds'],voice_id=voice['id'],technical_pass=check['technical_pass'],text_status=check['text_status'])
  if not check['technical_pass']:emit('progress',message='录音已保存，技术差异保留待复核；不阻塞其他配音，也不自动重录或标为合格。')
  return meta
 except BaseException as error:
  state['status']='interrupted' if isinstance(error,(InterruptedError,KeyboardInterrupt)) else 'failed'
  state['last_error']=dict(type=type(error).__name__,message=str(error));write_json(statepath,state);raise
 finally:
  process_signals.signal(process_signals.SIGTERM,old_term)
  if model is not None:del model;mx.clear_cache()
