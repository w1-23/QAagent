"""Task-scoped persistent manifests referencing the existing session file library."""
from .repository import Repository, WorkspaceConflict, purge_owner
from .service import TaskWorkspaceService

__all__ = ["Repository", "TaskWorkspaceService", "WorkspaceConflict", "purge_owner"]
