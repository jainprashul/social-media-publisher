import argparse,json
from .config import WorkspaceConfig
from .registry import Registry
from .ingest import ingest
from .derivatives import derive
from .validation import validate
from .orchestration import prepare,publish
from .approval import approve,job_fingerprint
from .profiles import PROFILES
from .previews import thumbnail,contact_sheet
from .captions import register_caption
from .voiceover import register_voiceover
def main(argv=None):
 p=argparse.ArgumentParser(prog='media-pipeline'); p.add_argument('--workspace',default='.'); sub=p.add_subparsers(dest='cmd',required=True)
 sub.add_parser('init'); x=sub.add_parser('ingest'); x.add_argument('file'); x.add_argument('--project',required=True); x.add_argument('--role',default='master'); x.add_argument('--source',default='local'); x.add_argument('--source-task'); x.add_argument('--prompt')
 x=sub.add_parser('derive'); x.add_argument('asset'); x.add_argument('--profile',required=True)
 x=sub.add_parser('validate'); x.add_argument('derivative'); x=sub.add_parser('thumbnail'); x.add_argument('derivative'); x=sub.add_parser('contact-sheet'); x.add_argument('derivative')
 x=sub.add_parser('caption'); x.add_argument('action',choices=['register']); x.add_argument('parent'); x.add_argument('file')
 x=sub.add_parser('voiceover'); x.add_argument('action',choices=['register']); x.add_argument('parent'); x.add_argument('file'); x.add_argument('--model'); x.add_argument('--tool')
 x=sub.add_parser('profile'); x.add_argument('action',choices=['show']); x.add_argument('name',nargs='?')
 x=sub.add_parser('prepare'); x.add_argument('derivative'); x.add_argument('--platform',required=True); x.add_argument('--destination',required=True); x.add_argument('--caption',default=''); x.add_argument('--visibility',default='public'); x.add_argument('--scheduled-at')
 x=sub.add_parser('approve'); x.add_argument('job'); x.add_argument('--actor',required=True); x.add_argument('--fingerprint',required=True)
 x=sub.add_parser('publish'); x.add_argument('job'); x.add_argument('--dry-run',action='store_true'); x=sub.add_parser('status'); x.add_argument('job'); x=sub.add_parser('asset'); x.add_argument('action',choices=['show','lineage']); x.add_argument('asset')
 for name in ('preview','reconcile'): x=sub.add_parser(name); x.add_argument('job')
 sub.add_parser('failures'); a=p.parse_args(argv); cfg=WorkspaceConfig(a.workspace).ensure(); r=Registry(cfg.db_path)
 if a.cmd=='init': out={'workspace':str(cfg.root),'db':str(cfg.db_path)}
 elif a.cmd=='ingest': out=dict(ingest(cfg,r,a.file,a.project,a.role,a.source,a.source_task,a.prompt))
 elif a.cmd=='derive': out=dict(derive(cfg,r,a.asset,a.profile))
 elif a.cmd=='validate': out=validate(cfg,r,a.derivative)
 elif a.cmd=='thumbnail': out=dict(thumbnail(cfg,r,a.derivative))
 elif a.cmd=='contact-sheet': out=dict(contact_sheet(cfg,r,a.derivative))
 elif a.cmd=='caption': out=dict(register_caption(cfg,r,a.parent,a.file))
 elif a.cmd=='voiceover': out=dict(register_voiceover(cfg,r,a.parent,a.file,a.model,a.tool))
 elif a.cmd=='profile': out={k:v.to_dict() for k,v in PROFILES.items()} if not a.name else PROFILES[a.name].to_dict()
 elif a.cmd=='prepare': out=dict(prepare(r,a.derivative,a.platform,a.destination,a.caption,a.visibility,a.scheduled_at))
 elif a.cmd=='approve': approve(r,a.job,a.actor,a.fingerprint); out=dict(r.job(a.job))
 elif a.cmd=='publish': out=dict(publish(cfg,r,a.job,dry_run=a.dry_run))
 elif a.cmd=='status': out=dict(r.job(a.job))
 elif a.cmd=='asset':
  if a.action=='show': out=dict(r.asset(a.asset))
  else:
   from .manifest import lineage; out=lineage(r,a.asset)
 elif a.cmd=='preview': out=dict(r.job(a.job)); out['fingerprint']=job_fingerprint(json.loads(out['external_json']))
 elif a.cmd=='reconcile': out=dict(r.job(a.job))
 elif a.cmd=='failures': out=[dict(x) for x in r.conn.execute("SELECT * FROM jobs WHERE state LIKE 'failed%'")]
 print(json.dumps(out,default=lambda x:x.to_dict() if hasattr(x,'to_dict') else str(x),sort_keys=True,indent=2)); return 0
if __name__=='__main__': main()
