"""Local catalog, hash-bound audition evidence and resumable production queue."""
import hashlib,json,sqlite3,time,uuid
from array import array
from contextlib import contextmanager
from pathlib import Path

SAMPLES=[
 'I left the light on for you. When I opened the door, the room was empty.',
 'The morning train arrived on time. A woman beside the window smiled and asked whether this seat was taken.'
]
STATES=['design','queued','generating','technical_failed','similarity_flagged','pending_listening','listening_confirmed']
def file_hash(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  while data:=f.read(1024*1024):h.update(data)
 return h.hexdigest()
class Library:
 def __init__(self,data,engine):
  self.data=Path(data);self.engine=Path(engine);self.root=self.data/'VoiceLibrary';self.root.mkdir(parents=True,exist_ok=True)
  self.db=sqlite3.connect(self.root/'library.sqlite',timeout=20);self.db.row_factory=sqlite3.Row
  self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA foreign_keys=ON')
  self.db.executescript('''CREATE TABLE IF NOT EXISTS voices(id TEXT PRIMARY KEY, profile TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE, status TEXT NOT NULL, updated REAL NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS auditions(id TEXT PRIMARY KEY, voice_id TEXT NOT NULL REFERENCES voices(id), profile_hash TEXT NOT NULL, evidence TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS queue(voice_id TEXT PRIMARY KEY REFERENCES voices(id), state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, voice_id TEXT NOT NULL, audition_id TEXT NOT NULL, decision TEXT NOT NULL, evidence TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS anchors(voice_id TEXT PRIMARY KEY,profile_hash TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,text TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS speaker_vectors(voice_id TEXT PRIMARY KEY,audition_id TEXT NOT NULL,vector BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
  catalog=self.engine/'voice-catalog.json';revision=file_hash(catalog)
  old=self.db.execute("SELECT value FROM metadata WHERE key='catalog_sha256'").fetchone()
  if not old or old[0]!=revision:
   rows=json.loads(catalog.read_text())
   with self.db:
    for v in rows:
     # A new catalog must never silently overwrite a profile with reviewed evidence.
     self.db.execute('INSERT OR IGNORE INTO voices VALUES(?,?,?,?,?,NULL)',(v['id'],json.dumps(v,ensure_ascii=False),v['fingerprint'],'design',time.time()))
    self.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('catalog_sha256',revision))
  if not self.db.execute("SELECT 1 FROM metadata WHERE key='speaker_index_v2'").fetchone():
   with self.db:
    for a in self.db.execute('SELECT a.voice_id,a.id,a.evidence FROM auditions a WHERE a.created=(SELECT MAX(b.created) FROM auditions b WHERE b.voice_id=a.voice_id)'):
     evidence=json.loads(a['evidence'])
     if evidence.get('pipeline_version')=='fixed-identity-exact-asr-v2' and evidence.get('technical_pass') and evidence.get('speaker_centroid'):
      self.db.execute('INSERT OR REPLACE INTO speaker_vectors VALUES(?,?,?)',(a['voice_id'],a['id'],array('f',evidence['speaker_centroid']).tobytes()))
    self.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('speaker_index_v2','1'))
 def get(self,key):
  row=self.db.execute('SELECT * FROM voices WHERE id=?',(key,)).fetchone()
  if row is None:raise ValueError('未知声音选项')
  return dict(row)|{'profile':json.loads(row['profile'])}
 def anchor(self,key):
  row=self.db.execute('SELECT * FROM anchors WHERE voice_id=?',(key,)).fetchone()
  if not row:return None
  a=dict(row)
  if a['profile_hash']!=self.get(key)['fingerprint'] or file_hash(a['path'])!=a['sha256']:raise ValueError('固定参考声已变化，不能继续使用旧身份')
  return a
 def save_anchor(self,key,sample):
  if not sample['technical_pass']:raise ValueError('参考声全文检查未通过')
  existing=self.anchor(key)
  if existing:return existing
  with self.db:self.db.execute('INSERT INTO anchors VALUES(?,?,?,?,?,?)',(key,self.get(key)['fingerprint'],sample['path'],file_hash(sample['path']),sample['expected'],time.time()))
  return self.anchor(key)
 def paused(self):
  row=self.db.execute("SELECT value FROM metadata WHERE key='queue_paused'").fetchone()
  return bool(row and row[0]=='1')
 def pause(self,value):
  with self.db:self.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('queue_paused','1' if value else '0'))
 def summary(self):
  counts={r[0]:r[1] for r in self.db.execute('SELECT status,COUNT(*) FROM voices GROUP BY status')}
  return dict(paused=self.paused(),design_recipes=sum(counts.values()),by_status=counts,generated_profiles=self.db.execute('SELECT COUNT(DISTINCT voice_id) FROM auditions').fetchone()[0],listening_confirmed=counts.get('listening_confirmed',0),distinct_voice_count_verified=False,queue_remaining=self.db.execute("SELECT COUNT(*) FROM queue WHERE state IN ('queued','generating')").fetchone()[0])
 def search(self,query='',state='',limit=150,offset=0):
  limit=min(200,max(1,int(limit)));offset=max(0,int(offset));clauses=[];args=[]
  for token in query.split():clauses.append('(id LIKE ? OR profile LIKE ?)');args.extend(['%'+token+'%']*2)
  if state:clauses.append('status=?');args.append(state)
  where=' WHERE '+' AND '.join(clauses) if clauses else ''
  total=self.db.execute('SELECT COUNT(*) FROM voices'+where,args).fetchone()[0]
  rows=self.db.execute('SELECT * FROM voices'+where+' ORDER BY CASE status WHEN \'listening_confirmed\' THEN 0 WHEN \'pending_listening\' THEN 1 ELSE 2 END,rowid LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
  return dict(total=total,offset=offset,limit=limit,voices=[json.loads(r['profile'])|{'status':r['status'],'preset_listening_confirmed':json.loads(r['profile']).get('approved',False)} for r in rows],summary=self.summary())
 def enqueue(self,keys):
  with self.db:
   for key in keys:
    row=self.get(key)
    if row['status']=='listening_confirmed':continue
    latest=self.latest(key)
    if row['status'] in ('pending_listening','similarity_flagged') and latest and latest['evidence'].get('pipeline_version')=='fixed-identity-exact-asr-v2':continue
    self.db.execute("INSERT INTO queue VALUES(?,'queued',0,?) ON CONFLICT(voice_id) DO UPDATE SET state='queued',updated=excluded.updated",(key,time.time()))
    self.db.execute("UPDATE voices SET status='queued',updated=?,error=NULL WHERE id=?",(time.time(),key))
 def next(self):
  # Caller holds a separate OS queue lock throughout the production run.
  row=self.db.execute("SELECT voice_id FROM queue WHERE state IN ('queued','generating') ORDER BY CASE state WHEN 'generating' THEN 0 ELSE 1 END,updated,voice_id LIMIT 1").fetchone()
  return row[0] if row else None
 def state(self,key,state,error=None):
  if state not in STATES:raise ValueError('未知状态')
  with self.db:
   self.db.execute('UPDATE voices SET status=?,updated=?,error=? WHERE id=?',(state,time.time(),error,key))
   self.db.execute('UPDATE queue SET state=?,updated=?,attempts=attempts+? WHERE voice_id=?',(state,time.time(),int(state=='generating'),key))
 def record(self,key,evidence):
  row=self.get(key);aid=str(uuid.uuid4())
  with self.db:
   self.db.execute('INSERT INTO auditions VALUES(?,?,?,?,?)',(aid,key,row['fingerprint'],json.dumps(evidence,ensure_ascii=False),time.time()))
   self.db.execute('DELETE FROM speaker_vectors WHERE voice_id=?',(key,))
   if evidence.get('pipeline_version')=='fixed-identity-exact-asr-v2' and evidence.get('technical_pass') and evidence.get('speaker_centroid'):
    self.db.execute('INSERT INTO speaker_vectors VALUES(?,?,?)',(key,aid,array('f',evidence['speaker_centroid']).tobytes()))
  self.state(key,'technical_failed' if not evidence['technical_pass'] else 'similarity_flagged' if evidence.get('similarity_flagged') else 'pending_listening')
  return aid
 def latest(self,key):
  row=self.db.execute('SELECT * FROM auditions WHERE voice_id=? ORDER BY created DESC LIMIT 1',(key,)).fetchone()
  return dict(row)|{'evidence':json.loads(row['evidence'])} if row else None
 def review(self,key,audition_id,decision):
  if decision not in ('accept','reject'):raise ValueError('未知验收选项')
  row=self.get(key);a=self.latest(key)
  if not a or a['id']!=audition_id or a['profile_hash']!=row['fingerprint']:raise ValueError('声音或试听已变化，请重新试听当前版本')
  e=a['evidence']
  anchor=e.get('fixed_reference')
  if anchor and self.anchor(key)['sha256']!=anchor['sha256']:raise ValueError('固定参考声音已变化，不能沿用旧试听')
  for sample in e['samples']:
   if file_hash(sample['path'])!=sample['sha256']:raise ValueError('试听文件已变化，不能沿用旧验收')
  if decision=='accept' and e.get('pipeline_version')!='fixed-identity-exact-asr-v2':raise ValueError('旧版试听检查不完整，请重新制作两段试听')
  if decision=='accept' and (not e['technical_pass'] or e.get('similarity_flagged')):raise ValueError('技术检查未通过或与已有声音过于相似，不能确认入库')
  with self.db:self.db.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?)',(str(uuid.uuid4()),key,audition_id,decision,json.dumps(dict(user_explicit_confirmation=True,profile_hash=row['fingerprint'],samples=[dict(path=s['path'],sha256=s['sha256']) for s in e['samples']])),time.time()))
  self.state(key,'listening_confirmed' if decision=='accept' else 'technical_failed','用户未通过听感' if decision=='reject' else None)
  return self.summary()
