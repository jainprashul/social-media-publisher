import json
from pathlib import Path
from durable_media.captions import validate_caption
from durable_media.config import WorkspaceConfig
from durable_media.registry import Registry
from durable_media.profiles import PROFILES

def test_profiles_have_stable_fingerprints():
    assert set(PROFILES)=={'instagram_reel_v1','youtube_short_v1','linkedin_video_v1','discord_preview_v1','audio_voiceover_v1'}
    assert all(len(p.fingerprint())==64 for p in PROFILES.values())

def test_caption_validation_rejects_overlap(tmp_path):
    p=tmp_path/'x.srt'; p.write_text('1\n00:00:00,000 --> 00:00:02,000\nOne\n\n2\n00:00:01,000 --> 00:00:03,000\nTwo\n',encoding='utf-8')
    try: validate_caption(p)
    except ValueError as e: assert 'overlap' in str(e)
    else: assert False

def test_registry_artifact_migration(tmp_path):
    r=Registry(tmp_path/'registry.sqlite3'); cols={x[1] for x in r.conn.execute('pragma table_info(artifacts)')}
    assert {'artifact_id','kind','parent_id','provenance_json'} <= cols
    assert r.artifacts()==[]


def test_short_preview_commands_emit_and_register_a_frame(tmp_path, monkeypatch):
    """A short clip must not depend on a one-second seek or five-second fps."""
    from durable_media import previews

    cfg = WorkspaceConfig(tmp_path).ensure()
    registry = Registry(cfg.db_path)
    parent = 'deriv-short'
    source = cfg.root / 'data' / 'derivatives' / parent / 'short.mp4'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'fake-media')
    registry.derivative = lambda _id: {'path': str(source.relative_to(cfg.root))}
    calls = []

    def fake_ffmpeg(args, workspace):
        calls.append(args)
        if 'fps=1/5,scale=320:-1,tile=3x3' not in args:
            Path(args[-1]).write_bytes(b'one-frame-jpeg')
        return {'returncode': 0, 'stderr': '', 'command': ['ffmpeg', *map(str, args)], 'version': 'fake'}

    monkeypatch.setattr(previews, 'run_ffmpeg', fake_ffmpeg)
    thumbnail = previews.thumbnail(cfg, registry, parent)
    sheet = previews.contact_sheet(cfg, registry, parent)

    assert (cfg.root / thumbnail['path']).is_file()
    assert (cfg.root / sheet['path']).is_file()
    assert {'thumbnail', 'contact_sheet'} == {row['kind'] for row in registry.artifacts()}
    assert ['-ss', '0'] == calls[0][1:3]
    assert 'select=eq(n\\,0),scale=320:-1' in calls[2]
