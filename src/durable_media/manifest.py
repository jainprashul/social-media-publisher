import json
from .security import redact
def build(project, source, assets, derivatives=(), publishing=()): return redact({'schema_version':1,'project_id':project,'source':source,'assets':[dict(x) for x in assets],'derivatives':[dict(x) for x in derivatives],'publishing':[dict(x) for x in publishing]})
def dumps(manifest): return json.dumps(redact(manifest),sort_keys=True,indent=2)+"\n"
def lineage(registry,asset_id):
 a=registry.asset(asset_id); out=[dict(a)] if a else []
 for d in registry.conn.execute('SELECT * FROM derivatives WHERE asset_id=?',(asset_id,)): out.append(dict(d))
 return out
