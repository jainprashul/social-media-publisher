from .derivatives import register_artifact
from .media_metadata import extract
def register_voiceover(cfg,registry,parent_id,path,model=None,tool=None):
 meta=extract(path,cfg.root); audio=meta.get('audio',{}); info={'media':meta,'sample_rate':audio.get('sample_rate'),'channels':audio.get('channels'),'codec':audio.get('codec_name')}
 return register_artifact(cfg,registry,parent_id,'voiceover',path,info,{'model':model,'tool':tool})
