import hashlib,json

APPROVAL_FIELDS = ('derivative_sha256','platform','destination','caption','visibility','scheduled_at')

def fingerprint(d):
 return hashlib.sha256(json.dumps(d,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def canonical_approval_payload(d):
 return {k:d.get(k) for k in APPROVAL_FIELDS}

def job_fingerprint(d):
 return fingerprint(canonical_approval_payload(d))

def approve(registry,job_id,actor,fp):
 j=registry.job(job_id)
 if not j or j['state']!='awaiting_approval': raise ValueError('job not awaiting approval')
 payload=json.loads(j['external_json'])
 expected=job_fingerprint(payload)
 if fp!=expected: raise ValueError('stale or invalid approval fingerprint')
 registry.approve(job_id,actor,fp)
