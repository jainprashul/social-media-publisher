import hashlib, mimetypes, shutil, os, json
from pathlib import Path
from .registry import uid, now
MAGIC={b'\x89PNG':'image/png',b'\xff\xd8\xff':'image/jpeg',b'RIFF':'image/x-riff',b'\x1aE\xdf\xa3':'video/webm'}
def sha256(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()
def detect_mime(path):
 with open(path,'rb') as f: head=f.read(16)
 for magic,mime in MAGIC.items():
  if head.startswith(magic): return mime
 return mimetypes.guess_type(path)[0] or 'application/octet-stream'
def ingest(cfg,registry,source,project,role='master',source_type='local',source_task=None,prompt=None,prompt_hash=None,agent=None,context_id=None,model=None,tool=None,original_path=None):
 p=Path(source).expanduser().resolve()
 if not p.is_file() or not os.access(p,os.R_OK): raise ValueError('source is missing or unreadable')
 if p.stat().st_size==0: raise ValueError('zero-byte source')
 digest=sha256(p); aid=uid('asset',digest+project)
 existing=registry.asset(aid)
 if existing and (cfg.root/existing['path']).is_file(): return existing
 ext=p.suffix.lower() or '.bin'; rel=f'data/assets/{aid}/master{ext}'; dest=cfg.safe(cfg.root/rel); dest.parent.mkdir(parents=True,exist_ok=True)
 shutil.copy2(p,dest); os.chmod(dest,0o440)
 mime=detect_mime(dest); kind=mime.split('/')[0] if '/' in mime else 'file'
 if prompt_hash is None and prompt: prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()
 src={'type':source_type,'task_id':source_task,'prompt_hash':prompt_hash}
 if agent: src['agent']=agent
 if context_id: src['context_id']=context_id
 if model: src['model']=model
 if tool: src['tool']=tool
 from .media_metadata import extract
 media={'original_name':p.name,'original_path':original_path or str(p),'extension':ext,'media':extract(dest,cfg.root)}
 if source_type=='a2a':
  media['a2a']={'task_id':source_task,'agent':agent,'prompt_hash':prompt_hash,'original_path':original_path or str(p)}
 registry.add_asset(asset_id=aid,project=project,kind=kind,role=role,path=rel,sha256=digest,mime=mime,bytes=dest.stat().st_size,created_at=now(),source_json=json.dumps(src),metadata_json=json.dumps(media))
 return registry.asset(aid)
