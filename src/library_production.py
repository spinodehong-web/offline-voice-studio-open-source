"""Single GPU resumable audition queue. No auto-acceptance, payment or network."""
import fcntl,json,os,shutil,time,uuid
from pathlib import Path
from voice_library import Library,SAMPLES,file_hash

def run_queue(data,engine,resources,emit,synthesize,gpu_lock,limit=0):
 from audition_qa import QualityCheck
 if not (Path(data)/'Models/qwen3-voice-base/model.safetensors').is_file():raise ValueError('请先导入 VoiceModel-Identity.zip，队列保留等待模型')
 lib=Library(data,engine);lockfile=lib.root/'production.lock';count=0;qa=QualityCheck(resources)
 with lockfile.open('a') as queue_lock:
  try:fcntl.flock(queue_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise ValueError('声音库队列已经在运行')
  while not limit or count<limit:
   if lib.paused():emit('progress',message='声音库队列已暂停，未完成任务保留。');break
   key=lib.next()
   if key is None:break
   if shutil.disk_usage(data).free<20*1024**3:raise ValueError('磁盘可用空间不足20GB，已保留队列，暂停生成')
   lib.state(key,'generating');voice=lib.get(key)['profile'];emit('progress',message=f'制作声音试听 {count+1}：{voice["name"]}')
   try:
    with gpu_lock(wait=True):
     samples=[]
     for index,text in enumerate(SAMPLES):
      attempts=[]
      for take in range(3):
       result=synthesize(dict(text=text,voice_id=key,take=take),notify=False)
       path=Path(result['path']);sample=qa.sample(path,text);sample.update(path=str(path),sha256=file_hash(path));attempts.append(sample.copy())
       if sample['technical_pass']:break
      sample['attempts']=attempts;samples.append(sample)
     evidence=qa.evidence(samples,lib,key);evidence['fixed_reference']=lib.anchor(key);evidence['pipeline_version']='fixed-identity-exact-asr-v2';aid=lib.record(key,evidence)
     emit('audition_done',voice_id=key,audition_id=aid,status=lib.get(key)['status'],summary=lib.summary())
   except Exception as e:
    lib.state(key,'technical_failed',str(e));emit('progress',message=f'声音 {key} 检查失败，证据已保留：{e}')
   count+=1
  emit('library_done',processed=count,summary=lib.summary())
