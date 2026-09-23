import sqlite3,json,hashlib
from datetime import datetime,timezone
from pathlib import Path
def now(): return datetime.now(timezone.utc).isoformat()
def uid(prefix,payload=None):
 raw=(payload or f'{prefix}:{now()}').encode(); return prefix+'_'+hashlib.sha256(raw).hexdigest()[:16]
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
  for table,cols in [('assets',[('metadata_json','TEXT')]),('derivatives',[('validation_json','TEXT')])]:
   existing={r[1] for r in self.conn.execute(f'PRAGMA table_info({table})')}
   for col,typ in cols:
    if col not in existing:self.conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {typ}')
  self.conn.commit()
 def add_asset(self,**kw): self.conn.execute('INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?)',tuple(kw[k] for k in ('asset_id','project','kind','role','path','sha256','mime','bytes','created_at','source_json','metadata_json'))); self.conn.commit()
 def asset(self,i): return self.conn.execute('SELECT * FROM assets WHERE asset_id=?',(i,)).fetchone()
 def assets(self): return self.conn.execute('SELECT * FROM assets').fetchall()
 def add_derivative(self,**kw): self.conn.execute('INSERT INTO derivatives VALUES(?,?,?,?,?,?,?,?)',tuple(kw[k] for k in ('derivative_id','asset_id','parent_sha256','path','sha256','profile','operations_json','validation_json'))); self.conn.commit()
 def derivative(self,i): return self.conn.execute('SELECT * FROM derivatives WHERE derivative_id=?',(i,)).fetchone()
 def find_derivative(self,asset_id,profile,parent_sha256): return self.conn.execute('SELECT * FROM derivatives WHERE asset_id=? AND profile=? AND parent_sha256=?',(asset_id,profile,parent_sha256)).fetchone()
 def add_artifact(self,**kw):
  vals=tuple(kw[k] for k in ('artifact_id','kind','parent_id','path','sha256','mime','bytes','metadata_json','provenance_json','created_at','validation_json'))
  self.conn.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?)',vals); self.conn.commit(); return self.artifact(kw['artifact_id'])
 def artifact(self,i): return self.conn.execute('SELECT * FROM artifacts WHERE artifact_id=?',(i,)).fetchone()
 def artifacts(self,parent_id=None): return self.conn.execute('SELECT * FROM artifacts'+(' WHERE parent_id=?' if parent_id else ''),((parent_id,) if parent_id else ())).fetchall()
 def add_job(self,**kw): self.conn.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',tuple(kw[k] for k in ('job_id','derivative_id','platform','destination','caption','visibility','scheduled_at','idempotency_key','state','external_json','created_at'))); self.conn.commit()
 def job(self,i): return self.conn.execute('SELECT * FROM jobs WHERE job_id=?',(i,)).fetchone()
 def approval(self,i): return self.conn.execute('SELECT * FROM approvals WHERE job_id=?',(i,)).fetchone()
 def approve(self,job_id,actor,fingerprint): self.conn.execute('INSERT OR REPLACE INTO approvals VALUES(?,?,?,?)',(job_id,actor,now(),fingerprint)); self.conn.execute('UPDATE jobs SET state=? WHERE job_id=?',('approved',job_id)); self.conn.commit()
 def transition(self,j,state,actor='system',reason='',metadata=None): self.conn.execute('UPDATE jobs SET state=? WHERE job_id=?',(state,j)); self.conn.execute('INSERT INTO transitions(job_id,state,actor,reason,metadata_json,at) VALUES(?,?,?,?,?,?)',(j,state,actor,reason,json.dumps(metadata or {}),now())); self.conn.commit()
 def transitions(self,j): return self.conn.execute('SELECT * FROM transitions WHERE job_id=? ORDER BY id',(j,)).fetchall()
 def receipt(self,key): return self.conn.execute('SELECT * FROM receipts WHERE idempotency_key=? AND verified=1',(key,)).fetchone()
 def add_receipt(self,**kw): self.conn.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?,?,?)',tuple(kw[k] for k in ('receipt_id','job_id','idempotency_key','external_id','url','verified','payload_json','created_at'))); self.conn.commit()
 def consistency(self,root):
  out=[]
  for r in self.assets():
   p=Path(root)/r['path']; reason='missing' if not p.exists() else ('hash_drift' if hashlib.sha256(p.read_bytes()).hexdigest()!=r['sha256'] else None)
   if reason: out.append({'asset_id':r['asset_id'],'reason':reason})
  return out
