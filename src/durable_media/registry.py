import sqlite3, json, hashlib
from datetime import datetime, timezone
from pathlib import Path
from .security import redact

def now(): return datetime.now(timezone.utc).isoformat()
def uid(prefix,payload=None):
    raw=(payload or f'{prefix}:{now()}').encode(); return prefix+'_'+hashlib.sha256(raw).hexdigest()[:16]

class InvalidTransition(ValueError): pass

TRANSITIONS = {
 'awaiting_approval': {'approved','rejected','cancelled'},
 'approved': {'queued','cancelled','awaiting_approval'},
 'queued': {'uploading','failed_retryable','failed_permanent','unknown_remote','cancelled'},
 'uploading': {'processing','failed_retryable','failed_permanent','unknown_remote','cancelled'},
 'processing': {'publishing','failed_retryable','failed_permanent','unknown_remote','cancelled'},
 'publishing': {'verifying','failed_retryable','failed_permanent','unknown_remote','cancelled'},
 'verifying': {'published','failed_retryable','failed_permanent','unknown_remote'},
 'failed_retryable': {'queued','cancelled','failed_permanent'},
 'unknown_remote': {'published','failed_permanent','failed_retryable','cancelled'},
 'published': set(), 'failed_permanent': set(), 'rejected': set(), 'cancelled': set(),
}

class Registry:
 def __init__(self,db):
  self.db=Path(db); self.db.parent.mkdir(parents=True,exist_ok=True); self.conn=sqlite3.connect(self.db); self.conn.row_factory=sqlite3.Row; self.init()
 def init(self):
  self.conn.executescript('''CREATE TABLE IF NOT EXISTS assets(asset_id TEXT PRIMARY KEY,project TEXT,kind TEXT,role TEXT,path TEXT,sha256 TEXT,mime TEXT,bytes INTEGER,created_at TEXT,source_json TEXT,metadata_json TEXT);
  CREATE TABLE IF NOT EXISTS derivatives(derivative_id TEXT PRIMARY KEY,asset_id TEXT,parent_sha256 TEXT,path TEXT,sha256 TEXT,profile TEXT,operations_json TEXT,validation_json TEXT);
  CREATE TABLE IF NOT EXISTS artifacts(artifact_id TEXT PRIMARY KEY,kind TEXT,parent_id TEXT,path TEXT,sha256 TEXT,mime TEXT,bytes INTEGER,metadata_json TEXT,provenance_json TEXT,created_at TEXT,validation_json TEXT);
  CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY,derivative_id TEXT,platform TEXT,destination TEXT,caption TEXT,visibility TEXT,scheduled_at TEXT,idempotency_key TEXT,state TEXT,external_json TEXT,created_at TEXT);
  CREATE TABLE IF NOT EXISTS approvals(job_id TEXT PRIMARY KEY,actor TEXT,approved_at TEXT,fingerprint TEXT);
  CREATE TABLE IF NOT EXISTS receipts(receipt_id TEXT PRIMARY KEY,job_id TEXT,idempotency_key TEXT,external_id TEXT,url TEXT,verified INTEGER,payload_json TEXT,created_at TEXT);
  CREATE TABLE IF NOT EXISTS transitions(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT,state TEXT,actor TEXT,reason TEXT,metadata_json TEXT,at TEXT);''')
  migrations = {'assets': [('metadata_json','TEXT')], 'derivatives':[('validation_json','TEXT')]}
  migrations['jobs']=[('attempt_count','INTEGER NOT NULL DEFAULT 0'),('next_attempt_at','TEXT'),('retry_limit','INTEGER NOT NULL DEFAULT 3'),('last_error_class','TEXT'),('remote_metadata_json','TEXT'),('approval_payload_json','TEXT'),('approval_fingerprint','TEXT')]
  migrations['transitions']=[('attempt','INTEGER NOT NULL DEFAULT 0'),('retryable','INTEGER NOT NULL DEFAULT 0')]
  migrations['receipts']=[('platform','TEXT'),('account','TEXT'),('verified_at','TEXT')]
  for table, cols in migrations.items():
   existing={r[1] for r in self.conn.execute(f'PRAGMA table_info({table})')}
   for col,typ in cols:
    if col not in existing: self.conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {typ}')
  self.conn.commit()
 def add_asset(self,**kw): self.conn.execute('INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?)',tuple(kw[k] for k in ('asset_id','project','kind','role','path','sha256','mime','bytes','created_at','source_json','metadata_json'))); self.conn.commit()
 def asset(self,i): return self.conn.execute('SELECT * FROM assets WHERE asset_id=?',(i,)).fetchone()
 def assets(self): return self.conn.execute('SELECT * FROM assets').fetchall()
 def add_derivative(self,**kw): self.conn.execute('INSERT INTO derivatives VALUES(?,?,?,?,?,?,?,?)',tuple(kw[k] for k in ('derivative_id','asset_id','parent_sha256','path','sha256','profile','operations_json','validation_json'))); self.conn.commit()
 def derivative(self,i): return self.conn.execute('SELECT * FROM derivatives WHERE derivative_id=?',(i,)).fetchone()
 def find_derivative(self,asset_id,profile,parent_sha256): return self.conn.execute('SELECT * FROM derivatives WHERE asset_id=? AND profile=? AND parent_sha256=?',(asset_id,profile,parent_sha256)).fetchone()
 def add_artifact(self,**kw):
  vals=tuple(kw[k] for k in ('artifact_id','kind','parent_id','path','sha256','mime','bytes','metadata_json','provenance_json','created_at','validation_json')); self.conn.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?)',vals); self.conn.commit(); return self.artifact(kw['artifact_id'])
 def artifact(self,i): return self.conn.execute('SELECT * FROM artifacts WHERE artifact_id=?',(i,)).fetchone()
 def artifacts(self,parent_id=None): return self.conn.execute('SELECT * FROM artifacts'+(' WHERE parent_id=?' if parent_id else ''),((parent_id,) if parent_id else ())).fetchall()
 def add_job(self,**kw):
  cols=('job_id','derivative_id','platform','destination','caption','visibility','scheduled_at','idempotency_key','state','external_json','created_at')
  self.conn.execute('INSERT INTO jobs('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',tuple(kw[k] for k in cols)); self.conn.commit()
 def job(self,i): return self.conn.execute('SELECT * FROM jobs WHERE job_id=?',(i,)).fetchone()
 def jobs(self): return self.conn.execute('SELECT * FROM jobs ORDER BY created_at').fetchall()
 def update_job(self,j,**fields):
  if fields:
   self.conn.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE job_id=?',(*fields.values(),j)); self.conn.commit()
 def approval(self,i): return self.conn.execute('SELECT * FROM approvals WHERE job_id=?',(i,)).fetchone()
 def approve(self,job_id,actor,fingerprint,payload=None):
  self.conn.execute('INSERT OR REPLACE INTO approvals VALUES(?,?,?,?)',(job_id,actor,now(),fingerprint)); self.conn.execute('UPDATE jobs SET state=?,approval_payload_json=?,approval_fingerprint=? WHERE job_id=?',('approved',json.dumps(redact(payload or {}),sort_keys=True),fingerprint,job_id)); self.conn.commit()
 def transition(self,j,state,actor='system',reason='',metadata=None, retryable=False, attempt=None):
  row=self.job(j)
  if not row: raise ValueError('unknown job')
  old=row['state']
  if state == old or state not in TRANSITIONS.get(old,set()): raise InvalidTransition(f'{old} -> {state} is not allowed')
  attempt = row['attempt_count'] if attempt is None else attempt
  safe=redact(metadata or {})
  self.conn.execute('UPDATE jobs SET state=?,attempt_count=?,last_error_class=?,remote_metadata_json=? WHERE job_id=?',(state,attempt, (safe.get('error_class') if isinstance(safe,dict) else None),json.dumps(safe,sort_keys=True),j))
  self.conn.execute('INSERT INTO transitions(job_id,state,actor,reason,metadata_json,at,attempt,retryable) VALUES(?,?,?,?,?,?,?,?)',(j,state,actor,reason,json.dumps(safe,sort_keys=True),now(),attempt,int(retryable)))
  self.conn.commit()
 def transitions(self,j): return self.conn.execute('SELECT * FROM transitions WHERE job_id=? ORDER BY id',(j,)).fetchall()
 def receipt(self,key): return self.conn.execute('SELECT * FROM receipts WHERE idempotency_key=? AND verified=1',(key,)).fetchone()
 def add_receipt(self,**kw):
  cols=('receipt_id','job_id','idempotency_key','external_id','url','verified','payload_json','created_at')
  self.conn.execute('INSERT INTO receipts('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',tuple(kw[k] for k in cols))
  extras={k:kw[k] for k in ('platform','account','verified_at') if k in kw}
  if extras: self.conn.execute('UPDATE receipts SET '+','.join(k+'=?' for k in extras)+' WHERE receipt_id=?',(*extras.values(),kw['receipt_id']))
  self.conn.commit()
 def consistency(self,root):
  out=[]
  for r in self.assets():
   p=Path(root)/r['path']; reason='missing' if not p.exists() else ('hash_drift' if hashlib.sha256(p.read_bytes()).hexdigest()!=r['sha256'] else None)
   if reason: out.append({'asset_id':r['asset_id'],'reason':reason})
  return out
