"""Owner-bound immutable references and task input lifetimes.

The existing account file index and resource-reference table are authoritative;
this module adds typed receipts and ties pin release to actual retained work.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ResourceRef:
    file_id: str
    sha256: str
    owner_user_id: int
    version: int = 1
    kind: str = "account_file"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "file_id": self.file_id, "sha256": self.sha256,
                "owner_user_id": self.owner_user_id, "version": self.version}


def pin_inputs(owner: int, resource_kind: str, resource_id: str,
               file_ids: Iterable[str], *, lease=None) -> list[ResourceRef]:
    from src.session.files import replace_resource_references, release_resource_references

    if lease is not None and lease.owner != int(owner):
        raise ValueError("资源租约与账号不匹配。")
    rows = replace_resource_references(owner, resource_kind, resource_id, file_ids)
    if lease is not None:
        try:
            lease.add_finalizer(lambda: release_resource_references(owner, resource_kind, resource_id))
        except BaseException:
            release_resource_references(owner, resource_kind, resource_id)
            raise
    return [ResourceRef(row["file_id"], row["sha256"], int(owner)) for row in rows]


@contextmanager
def resource_lease(owner: int, resource_kind: str, resource_id: str,
                   file_ids: Iterable[str]):
    from src.session.files import release_resource_references

    refs = pin_inputs(owner, resource_kind, resource_id, file_ids)
    try:
        yield refs
    finally:
        release_resource_references(owner, resource_kind, resource_id)
