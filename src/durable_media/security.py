import json,re
SECRET=re.compile(r'(authorization|access[_-]?token|client_secret|cookie|password|private_key|signed_url)',re.I)
def redact(value):
 if isinstance(value,dict): return {k:('[REDACTED]' if SECRET.search(k) else redact(v)) for k,v in value.items()}
 if isinstance(value,list): return [redact(x) for x in value]
 if isinstance(value,str) and ('Bearer ' in value or '://'+'' in value and 'token' in value.lower()): return '[REDACTED]'
 return value
def canonical(value): return json.dumps(redact(value),sort_keys=True,separators=(',',':'))
