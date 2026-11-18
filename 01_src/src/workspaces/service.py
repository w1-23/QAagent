"""Owner/session-bound public facade; constructing it performs no model or DB I/O."""
from __future__ import annotations

from .repository import Repository


class TaskWorkspaceService:
    """Create selects a new task; open observes; touch explicitly resumes/selects.

    Callers supply authenticated owner/session, never infer them from a file ID.
    Repository repeats account/session checks for every complete operation.
    """
    def __init__(self, owner: int, session: str, *, repository: Repository | None = None):
        self.owner, self.session = owner, session
        self.repository = repository if repository is not None else Repository()

    def create(self, *, task_id, request_id, saved=False, metadata=None):
        return self.repository.create(self.owner, self.session, task_id=task_id,
                                      request_id=request_id, saved=saved, metadata=metadata)

    def open(self, workspace_id=None):
        return self.repository.open(self.owner, self.session, workspace_id)

    def list(self, *, limit=50, offset=0):
        return self.repository.list(self.owner, self.session, limit=limit, offset=offset)

    def touch(self, workspace_id, *, expected_revision, make_current=True):
        return self.repository.touch(self.owner, self.session, workspace_id,
                                     expected_revision=expected_revision, make_current=make_current)

    def bind(self, workspace_id, *, path, file_id, expected_revision, expected_sha256,
             source=None, parents=None):
        return self.repository.bind(self.owner, self.session, workspace_id, path=path,
                                    file_id=file_id, expected_revision=expected_revision,
                                    expected_sha256=expected_sha256, source=source, parents=parents)

    def list_files(self, workspace_id, *, limit=50, offset=0, history=False):
        return self.repository.list_files(self.owner, self.session, workspace_id,
                                          limit=limit, offset=offset, history=history)

    def snapshot(self, workspace_id, *, task_id, expected_revision):
        return self.repository.snapshot(self.owner, self.session, workspace_id,
                                        task_id=task_id, expected_revision=expected_revision)

    def read_bytes(self, workspace_id, *, path, version=None, expected_sha256=None):
        return self.repository.read_bytes(self.owner, self.session, workspace_id,
                                          path=path, version=version, expected_sha256=expected_sha256)

    def set_retention(self, workspace_id, *, expected_revision, saved, pinned):
        return self.repository.set_retention(self.owner, self.session, workspace_id,
                                             expected_revision=expected_revision, saved=saved, pinned=pinned)

    def retain_execution(self, workspace_id, *, operation_id, task_id, expected_revision):
        return self.repository.retain_execution(self.owner, self.session, workspace_id,
                                                operation_id=operation_id, task_id=task_id,
                                                expected_revision=expected_revision)

    def release_execution(self, workspace_id, *, operation_id):
        return self.repository.release_execution(self.owner, self.session, workspace_id,
                                                 operation_id=operation_id)

    def inflight(self, workspace_id):
        return self.repository.inflight(self.owner, self.session, workspace_id)

    def select_expired(self, *, limit=50, offset=0):
        return self.repository.select_expired(self.owner, self.session, limit=limit, offset=offset)
