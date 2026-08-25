import pytest

from app.storage.json_store import JsonStore


@pytest.mark.asyncio
async def test_store_roundtrip(tmp_path):
    store = JsonStore(tmp_path)
    await store.write('x', {'value': 1})
    assert await store.read('x', {}) == {'value': 1}
    await store.update('x', {}, lambda x: x.update({'value': 2}))
    assert (await store.read('x', {}))['value'] == 2
