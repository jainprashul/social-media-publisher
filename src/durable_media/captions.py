import re
from .derivatives import register_artifact
_TIME=re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})")
def _ms(s):
 m=_TIME.fullmatch(s.strip())
 if not m: raise ValueError('invalid caption timestamp')
 return ((int(m[1])*60+int(m[2]))*60+int(m[3]))*1000+int(m[4])
def validate_caption(path,fmt=None,duration=None):
 text=open(path,encoding='utf-8-sig').read(); fmt=fmt or ('vtt' if str(path).lower().endswith('.vtt') else 'srt'); lines=text.splitlines(); cues=[]; i=0
 if fmt=='vtt':
  if not lines or lines[0].strip()!='WEBVTT': raise ValueError('VTT must start with WEBVTT')
  i=1
 while i<len(lines):
  if not lines[i].strip() or '-->' not in lines[i]: i+=1; continue
  if '-->' not in lines[i]: raise ValueError('invalid caption cue')
  left,right=[x.strip().split()[0] for x in lines[i].split('-->',1)]; start,end=_ms(left),_ms(right); i+=1; body=[]
  while i<len(lines) and lines[i].strip(): body.append(lines[i]); i+=1
  if end<=start or not body: raise ValueError('empty or non-positive caption cue')
  if cues and start<cues[-1][1]: raise ValueError('overlapping or non-monotonic caption cues')
  if duration is not None and end>duration*1000: raise ValueError('caption cue exceeds media duration')
  cues.append((start,end,'\n'.join(body)))
 if not cues: raise ValueError('caption file has no cues')
 return {'format':fmt,'cues':len(cues),'duration_ms':cues[-1][1]}
def register_caption(cfg,registry,parent_id,path):
 return register_artifact(cfg,registry,parent_id,'caption',path,validate_caption(path),{'validator':'durable_media.captions'})
