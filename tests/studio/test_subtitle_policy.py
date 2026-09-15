from types import SimpleNamespace

import pytest

from mlvideo.studio.jobs import JobExecutor


def test_render_rejects_global_union_of_moving_english_boxes():
    clip = {'clip': 'clip', 'raw_clip': 'raw', 'audio': 'audio', 'qa': 'qa'}
    plan = {'id': 'plan', 'bindings': {'utterances': 'units', 'translations': ['translation']}, 'payload': {
        'asset_sha512': 'asset', 'items': [{'unit_id': 'u'}],
        'bindings': {'canonical': 'clock', 'captions': 'captions'},
        'render_settings': {'preserve_source_english': True},
    }}
    executor = object.__new__(JobExecutor)
    executor.payload = {'plan_id': 'plan', 'selections': {'u': {'id': 'selection', 'payload': clip}}}
    executor.catalog = SimpleNamespace(get=lambda table, identity:
        plan if table == 'studio_plans' else {'plan_id': 'plan', 'payload': clip})
    calls = []

    def step(key, *args, **kwargs):
        calls.append(key)
        return {'timeline': 'timeline'}

    executor.step = step
    executor.value = lambda *args: {'cues': [
        {'boxes': [[100, 850, 1500, 910]]},
        {'boxes': [[200, 940, 1400, 1000]]},
    ]}
    with pytest.raises(ValueError, match='全片并集'):
        executor.render()
    assert calls == ['timeline']
