"""Pure path resolution; importing this module never touches runtime state."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class DataCapability:
    available: bool
    reason_code: str
    empty_allowed: bool


def resolve_project_root(repo: Path, raw: str | None = None) -> Path:
    """Validate the original override before resolving it, retaining agent/ default.

    Overrides configure a new process, not a live database migration. Application
    directories are forbidden even through a symlink. A repo-local agent/ remains
    supported; 'source tree' means executable/configuration directories.
    """
    repo = repo.resolve()
    if raw is None:
        return repo / "agent"
    if not raw.strip():
        raise ValueError("CITRUSQA_PROJECT_ROOT must not be empty")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError("CITRUSQA_PROJECT_ROOT must be an absolute path")
    if not candidate.is_dir():
        raise ValueError("CITRUSQA_PROJECT_ROOT must name an existing directory")
    resolved = candidate.resolve()
    forbidden = [repo / name for name in ("01_src", "01_源码_src", "02_config", "packages", "skills", ".git")]
    if resolved == repo or any(resolved.is_relative_to(p.resolve()) for p in forbidden):
        raise ValueError("CITRUSQA_PROJECT_ROOT must not be an application source directory")
    return resolved


def data_capability(data_dir: Path, *, allow_empty: bool = False) -> DataCapability:
    """Probe retrieval availability without creating directories or failing imports."""
    if not data_dir.is_dir():
        return DataCapability(False, "data_missing", allow_empty)
    if not (data_dir / "active_index.json").is_file():
        return DataCapability(False, "index_missing", allow_empty)
    return DataCapability(True, "index_manifest_present", allow_empty)


def require_retrieval_data(data_dir: Path, *, allow_empty: bool = False) -> DataCapability:
    capability = data_capability(data_dir, allow_empty=allow_empty)
    if not capability.available and not allow_empty:
        raise RuntimeError(capability.reason_code + ": configure corpus data or CITRUSQA_ALLOW_EMPTY_DATA=1")
    return capability
