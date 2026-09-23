import json
from .ingest import sha256
from .profiles import get_profile
from .media_metadata import extract
def validate(cfg,registry,did):
 d=registry.derivative(did)
 if not d: raise ValueError('unknown derivative')
 p=cfg.safe(cfg.root/d['path']); prof=get_profile(d['profile']); checks=[]
 def add(n,v,detail=None): checks.append({'name':n,'passed':bool(v),**({'detail':detail} if detail else {})})
 add('exists',p.is_file()); add('non_empty',p.is_file() and p.stat().st_size>0); add('hash',p.is_file() and sha256(p)==d['sha256']); add('mime',bool(d['path'].rsplit('.',1)[-1]))
 meta=extract(p,cfg.root); media=meta.get('video' if prof.kind=='video' else 'audio',{})
 if meta.get('available'):
  if prof.kind=='video':
   add('dimensions',media.get('width')==prof.width and media.get('height')==prof.height,media.get('width'))
   add('codec',media.get('codec_name')==prof.video_codec,media.get('codec_name')); add('fps',not prof.fps or abs(media.get('fps',0)-prof.fps)<1,media.get('fps'))
  else:
   add('sample_rate',not prof.sample_rate or int(media.get('sample_rate',0))==prof.sample_rate,media.get('sample_rate')); add('channels',not prof.channels or media.get('channels')==prof.channels,media.get('channels'))
  dur=float(meta.get('format',{}).get('duration',0) or 0); add('duration',not prof.max_duration or dur<=prof.max_duration,dur)
 add('file_size',not prof.max_bytes or (p.is_file() and p.stat().st_size<=prof.max_bytes),p.stat().st_size if p.exists() else 0)
 out={'status':'passed' if all(x['passed'] for x in checks) else 'failed','checks':checks,'profile':prof.to_dict()}
 registry.conn.execute('UPDATE derivatives SET validation_json=? WHERE derivative_id=?',(json.dumps(out,sort_keys=True),did)); registry.conn.commit(); return out
