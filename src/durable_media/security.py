import json,re
SECRET=re.compile(r'(authorization|access[_-]?token|client[_-]?secret|cookie|password|private[_-]?key|signed[_-]?url|oauth[_-]?code|api[_-]?key|token)',re.I)

def redact(value):
 if isinstance(value,dict): return {k:('[REDACTED]' if SECRET.search(k) else redact(v)) for k,v in value.items()}
 if isinstance(value,list): return [redact(x) for x in value]
 if isinstance(value,tuple): return tuple(redact(x) for x in value)
 if isinstance(value,str):
  if re.search(r'Bearer\s+\S+',value,re.I): return re.sub(r'Bearer\s+\S+','Bearer [REDACTED]',value,flags=re.I)
  if re.search(r'([?&](?:token|access_token|code|sig|signature)=)[^&]+',value,re.I): return re.sub(r'([?&](?:token|access_token|code|sig|signature)=)[^&]+',r'\1[REDACTED]',value,flags=re.I)
 return value

def canonical(value): return json.dumps(redact(value),sort_keys=True,separators=(',',':'))
