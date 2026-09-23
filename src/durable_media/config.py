from pathlib import Path

class WorkspaceConfig:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
    @property
    def db_path(self): return self.root / 'data' / 'registry.sqlite3'
    def ensure(self):
        self.root.mkdir(parents=True, exist_ok=True)
        for p in ('data/assets','data/derivatives','data/previews','data/receipts','data/logs','manifests','projects'):
            (self.root/p).mkdir(parents=True, exist_ok=True)
        return self
    def safe(self, path):
        p=Path(path).expanduser().resolve()
        if p != self.root and self.root not in p.parents: raise ValueError('path escapes workspace')
        return p
