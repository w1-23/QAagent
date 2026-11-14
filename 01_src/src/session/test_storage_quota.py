"""Account-wide indexing quotas: engineering fixtures, no production state."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from src.session.test_resource_references import load_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('CITRUS_ACCOUNT_STORAGE_BYTES', '10')
    monkeypatch.setenv('CITRUS_ACCOUNT_STORAGE_FILES', '2')
    return load_store(tmp_path)


def upload(store, content=b'12345', owner=7):
    return store.register_file(session_id='synthetic_quota_session', owner_user_id=owner,
                               filename='fixture.txt', content=content)


def test_concurrent_indexing_cannot_bypass_account_capacity(store):
    def attempt(_):
        try:
            upload(store)
            return True
        except store.StorageQuotaExceeded:
            return False
    with ThreadPoolExecutor(max_workers=4) as workers:
        successes = list(workers.map(attempt, range(4)))
    assert sum(successes) == 2
    assert store.storage_usage(7) == {'used_bytes': 10, 'file_count': 2, 'max_bytes': 10, 'max_files': 2}
    assert len(list(store.FILES_ROOT.rglob('*.txt'))) == 2
    other = upload(store, owner=8)
    assert store.get_account_file(other['file_id'], 8) is not None
    assert store.get_account_file(other['file_id'], 7) is None


def test_failure_removes_upload_and_does_not_delete_existing_bytes(store):
    first = upload(store, b'1234567890')
    with pytest.raises(store.StorageQuotaExceeded):
        upload(store, b'x')
    assert store.get_account_file(first['file_id'], 7)['path'].read_bytes() == b'1234567890'
    assert len(list(store.FILES_ROOT.rglob('*.txt'))) == 1


def test_generated_retry_at_quota_is_idempotent_and_growth_rejected(store, tmp_path):
    sid = 'synthetic_quota_session'
    path = tmp_path / 'state' / 'artifacts' / sid / 'export.txt'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'1234567890')
    kwargs = dict(session_id=sid, owner_user_id=7, filename=path.name,
                  storage_path=str(path), origin=store.ORIGIN_GENERATED, artifact_id='b' * 32)
    first = store.register_file(**kwargs)
    assert store.register_file(**kwargs)['file_id'] == first['file_id']
    path.write_bytes(b'12345678901')
    with pytest.raises(store.StorageQuotaExceeded):
        store.register_file(**kwargs)
    assert store.storage_usage(7)['used_bytes'] == 10
    assert path.exists()  # Generated-byte ownership belongs to the publisher.


def test_file_count_quota_and_configuration_fail_closed(store, monkeypatch):
    monkeypatch.setenv('CITRUS_ACCOUNT_STORAGE_BYTES', '1000')
    upload(store, b'1')
    upload(store, b'1')
    with pytest.raises(store.StorageQuotaExceeded):
        upload(store, b'1')
    monkeypatch.setenv('CITRUS_ACCOUNT_STORAGE_FILES', 'invalid')
    with pytest.raises(ValueError, match='quota configuration'):
        upload(store, b'1', owner=9)
