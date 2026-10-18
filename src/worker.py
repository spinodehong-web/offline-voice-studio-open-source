"""Private standalone worker. Only local files; no service ports or cloud fallback."""
import sys
sys.dont_write_bytecode=True
import os,json,hashlib,zipfile,fcntl,argparse,traceback,time,shutil
from pathlib import Path
from contextlib import contextmanager
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from voice_library import Library,SAMPLES,file_hash
RESOURCES=HERE.parent
DATA=Path(os.environ.get('OFFLINE_VOICE_DATA',str(Path.home()/'Library/Application Support/Offline Voice Studio Open Source')))
MODELS=DATA/'Models'
def emit(event,**data):print(json.dumps(dict(event=event,**data),ensure_ascii=False),flush=True)
def guard(event,args):
 if event in ('socket.connect','socket.connect_ex','socket.getaddrinfo','socket.sendto'):raise RuntimeError('离线配音禁止网络访问')
def manifest():return json.loads((RESOURCES/'model-manifest.json').read_text())
def health():
 approved_mode=(HERE/'approved-voices.json').is_file()
 missing=[k for k,v in manifest().items() if (k.startswith('qwen3-voice-base/') if approved_mode else not v.get('optional')) and (not (MODELS/k).is_file() or (MODELS/k).stat().st_size!=v['size'])]
 identity=[(MODELS/k).is_file() and (MODELS/k).stat().st_size==v['size'] for k,v in manifest().items() if k.startswith('qwen3-voice-base/')]
 return dict(ready=not missing,identity_ready=bool(identity) and all(identity),missing=missing,data_dir=str(DATA),architecture=__import__('platform').machine())
def import_pack(path):
 with gpu_lock():return _import_pack(path)
def _import_pack(path):
 expected=manifest();count=0
 with zipfile.ZipFile(path) as z:
  files=[i for i in z.infolist() if not i.is_dir()]
  if not files or any(i.filename not in expected or i.file_size!=expected[i.filename]['size'] for i in files):raise ValueError('不是本应用的模型包，或文件大小不匹配')
  for i in files:
   dest=MODELS/i.filename;dest.parent.mkdir(parents=True,exist_ok=True);tmp=dest.with_suffix(dest.suffix+'.importing');digest=hashlib.sha256()
   try:
    with z.open(i) as src,tmp.open('wb') as out:
     while chunk:=src.read(4*1024*1024):digest.update(chunk);out.write(chunk)
    if digest.hexdigest()!=expected[i.filename]['sha256']:raise ValueError('模型校验失败：'+i.filename)
    tmp.replace(dest);count+=1;emit('progress',message='已校验 '+i.filename)
   finally:
    if tmp.exists():tmp.unlink()
 # The optional Base model uses exactly the already imported speech tokenizer.
 # Reuse verified local files; no third download and no absolute project dependency.
 for key,value in expected.items():
  prefix='qwen3-voice-base/speech_tokenizer/'
  if not key.startswith(prefix):continue
  dest=MODELS/key;source=MODELS/('qwen3-voice-design/speech_tokenizer/'+key[len(prefix):])
  if not dest.exists() and source.is_file() and source.stat().st_size==value['size']:
   if hashlib.sha256(source.read_bytes()).hexdigest()!=value['sha256']:raise ValueError('共享语音编码器校验失败')
   dest.parent.mkdir(parents=True,exist_ok=True);tmp=dest.with_suffix(dest.suffix+'.importing');shutil.copy2(source,tmp);tmp.replace(dest)
 emit('imported',files=count,**health())
def voice_settings(request):
 voice=Library(DATA,HERE).get(request.get('voice_id','natural-female'))['profile']
 speed=float(request.get('speed',1));gap=float(request.get('paragraph_gap',.3))
 if not .8<=speed<=1.2 or not 0<=gap<=2:raise ValueError('语速或段落停顿超出范围')
 return voice,speed,gap
@contextmanager
def gpu_lock(wait=False):
 lockpath=Path('/tmp')/f'com.spinode.offline-voice-gpu-{os.getuid()}.lock'
 with lockpath.open('a') as lock:
  while True:
   try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
   except BlockingIOError:
    if not wait:raise ValueError('另一项本地配音正在生成，请稍后再试')
    emit('progress',message='等待当前本地配音释放 GPU，队列已保存…');time.sleep(10)
  try:yield
  finally:fcntl.flock(lock,fcntl.LOCK_UN)
def generate(request):
 with gpu_lock():
  from studio_release import catalog,generate as approved_generate
  if catalog(HERE):return approved_generate(request,HERE,RESOURCES,DATA,emit)
  return synthesize(request)
def synthesize(request,notify=True,design_reference=False):
 voice,speed,gap=voice_settings(request)
 info=health()
 if not info['ready']:raise ValueError('请先导入两个模型包')
 text=request.get('text','').strip()
 if not 1<=len(text)<=12000:raise ValueError('请输入1至12000个字符的英文文稿')
 DATA.mkdir(parents=True,exist_ok=True)
 for key in ['HF_TOKEN','HUGGING_FACE_HUB_TOKEN','OPENAI_API_KEY','ELEVENLABS_API_KEY']:os.environ.pop(key,None)
 os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1',TOKENIZERS_PARALLELISM='false')
 import mlx.core as mx,numpy as np,soundfile as sf
 from mlx_audio.tts.utils import load_model
 from finish import finish
 config=json.loads((HERE/'voice-profile.json').read_text());config['instruction']=voice['instruction'];config['version']=voice['id']+'-v1';config['pacing']['tempo']*=speed;emit('progress',message='正在加载本地声音模型…')
 lib=Library(DATA,HERE);reference=None;fixed=voice['id']!='natural-female' and not design_reference
 if fixed:
  if not (MODELS/'qwen3-voice-base/model.safetensors').is_file():raise ValueError('请先导入 VoiceModel-Identity.zip 固定声音模型包')
  reference=lib.anchor(voice['id'])
  if reference is None:
   from audition_qa import QualityCheck
   qa=QualityCheck(RESOURCES)
   for attempt in range(3):
    result=synthesize(dict(text=SAMPLES[0],voice_id=voice['id'],reference_attempt=attempt),notify=False,design_reference=True)
    sample=qa.sample(result['path'],SAMPLES[0]);sample['path']=result['path']
    if sample['technical_pass']:reference=lib.save_anchor(voice['id'],sample);break
   if reference is None:raise ValueError('原创参考声三次全文检查均未通过，已保留录音，不冒用其他声音')
  config['pacing']['tempo']=speed
 model=load_model(str(MODELS/('qwen3-voice-base' if fixed else 'qwen3-voice-design')));mx.random.seed(voice['seed']+int(request.get('reference_attempt',0))+int(request.get('take',0)))
 if fixed:reference_pcm,_=sf.read(reference['path'],dtype='float32')
 phrases=[]
 for paragraph in text.splitlines():
  words=paragraph.split()
  phrases.extend(' '.join(words[i:i+90]) for i in range(0,len(words),90))
 parts=[];rate=24000
 for i,phrase in enumerate(phrases):
  if not phrase:continue
  emit('progress',message=f'本地录制 {i+1}/{len(phrases)}…')
  chunks=[]
  results=model.generate(phrase,ref_audio=mx.array(reference_pcm),ref_text=reference['text'],lang_code='English',temperature=.65,max_tokens=1800,verbose=False) if fixed else model.generate_voice_design(phrase,instruct=config['instruction'],language='English',temperature=.65,max_tokens=1800,verbose=False)
  for r in results:chunks.append(np.asarray(r.audio,dtype='float32'));rate=r.sample_rate
  if not chunks:raise ValueError('模型未生成音频')
  parts.extend([np.concatenate(chunks),np.zeros(round(rate*gap),dtype='float32')])
 if not parts:raise ValueError('文稿没有可录制内容')
 signal=np.concatenate(parts[:-1]);peak=float(np.max(np.abs(signal)))
 if not np.isfinite(signal).all():raise ValueError('模型生成无效采样')
 if peak>.95:signal*=.95/peak
 del model;mx.clear_cache()
 import uuid
 folder=DATA/'Recordings'/str(uuid.uuid4());folder.mkdir(parents=True);output=folder/'narration.wav';(folder/'script.txt').write_text(text)
 emit('progress',message='正在整理节奏和检查音量…')
 signal,rate,report=finish(signal,rate,output,config,RESOURCES)
 sf.write(output,signal,rate,subtype='PCM_16')
 seconds=len(signal)/rate;meta=dict(text=text,actual_seed=voice['seed']+int(request.get('reference_attempt',0))+int(request.get('take',0)),voice_id=voice['id'],voice_name=voice['name'],preset_listening_confirmed=voice.get('approved',False),speed=speed,paragraph_gap=gap,engine='Qwen3-TTS Base 4bit MLX' if fixed else 'Qwen3-TTS VoiceDesign 4bit MLX',fixed_reference_used=fixed,reference_sha256=reference['sha256'] if fixed else None,reference_text=reference['text'] if fixed else None,offline=True,network='denied by audit hook',seconds=seconds,processing=report,preset_version=config['version'],actual_listening_performed=False)
 output.with_suffix('.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
 if notify:emit('complete',path=str(output),seconds=seconds,voice_id=voice['id'])
 return dict(path=str(output),seconds=seconds,voice_id=voice['id'])
def main():
 sys.addaudithook(guard)
 p=argparse.ArgumentParser();p.add_argument('--studio-project');p.add_argument('--health',action='store_true');p.add_argument('--import-pack');p.add_argument('--generate');p.add_argument('--verify-offline',action='store_true');p.add_argument('--library-request');p.add_argument('--run-library-queue',action='store_true');p.add_argument('--limit',type=int,default=0);a=p.parse_args()
 try:
  if a.limit<0:raise ValueError('队列数量不能为负数')
  if a.studio_project:
   from project_studio import run as studio_run
   with gpu_lock():studio_run(json.loads(Path(a.studio_project).read_text()),HERE,RESOURCES,DATA,emit)
  elif a.health:emit('health',**health())
  elif a.import_pack:import_pack(a.import_pack)
  elif a.generate:generate(json.loads(Path(a.generate).read_text()))
  elif a.library_request:
   req=json.loads(Path(a.library_request).read_text());action=req.get('action','list')
   from studio_release import catalog
   approved=catalog(HERE)
   if approved:
    if action!='list':raise ValueError('本版新增音色及试听队列保持暂停')
    rows=[dict(v['profile'],status='listening_confirmed') for v in approved['voices'] if all(t.lower() in (v['id']+' '+v['profile']['name']).lower() for t in req.get('query','').split())]
    emit('voices',voices=rows,total=len(rows),summary=dict(paused=True,listening_confirmed=len(approved['voices']),release_scope='user-confirmed-catalog'))
    return
   lib=Library(DATA,HERE)
   if action=='list':emit('voices',**lib.search(req.get('query',''),req.get('state',''),req.get('limit',150),req.get('offset',0)))
   elif action=='enqueue':lib.enqueue(req['voice_ids']);emit('queue_saved',summary=lib.summary())
   elif action in ('pause','resume'):lib.pause(action=='pause');emit('queue_saved',summary=lib.summary())
   elif action=='latest':emit('audition',voice_id=req['voice_id'],audition=lib.latest(req['voice_id']))
   elif action=='review':emit('review_saved',summary=lib.review(req['voice_id'],req['audition_id'],req['decision']))
   else:raise ValueError('未知声音库操作')
  elif a.run_library_queue:
   from studio_release import catalog
   if catalog(HERE):raise ValueError('本版新增音色及试听队列保持暂停')
   from library_production import run_queue
   run_queue(DATA,HERE,RESOURCES,emit,synthesize,gpu_lock,a.limit)
  elif a.verify_offline:
   sys.addaudithook(guard)
   import socket
   try:socket.getaddrinfo('example.com',443)
   except RuntimeError:emit('offline_verified',blocked=True)
   else:raise RuntimeError('Network guard failed')
  else:p.error('mode required')
 except Exception as e:emit('error',message=str(e));traceback.print_exc(file=sys.stderr);sys.exit(1)
if __name__=='__main__':main()
