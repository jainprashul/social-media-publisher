import json,re
from datetime import datetime,timezone
from .security import redact
class JsonLogger:
 def __init__(self,path): self.path=path
 def event(self,**fields):
  fields['at']=datetime.now(timezone.utc).isoformat()
  with open(self.path,'a') as f: f.write(json.dumps(redact(fields),sort_keys=True)+'\n')
def scan_package(root):
 bad=[]
 for p in root.rglob('*'):
  if p.is_file() and (p.name in ('.env','id_rsa') or p.suffix in ('.pem','.key')): bad.append(str(p))
 return bad
