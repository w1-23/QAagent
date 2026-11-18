"""Native Linux dedicated bounded-block-volume admission, no mount/provision code.

The trusted deployment administrator mounts a dedicated capped block filesystem.
This verifier reads fresh kernel mountinfo, sysfs block capacity and statvfs;
caller JSON, a directory quota counter or free-space polling are not a hard cap.
Windows/UNC/9p staging is explicitly unsupported by this admission mode.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys


class StorageUnavailable(RuntimeError):
    pass


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("hard_storage_integer_invalid")
    return value


def _no_links(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise StorageUnavailable("hard_storage_link_denied")


def _unescape(value):
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


@dataclass(frozen=True)
class VolumePolicy:
    root: Path
    maximum_bytes: int
    reserve_bytes: int = 8 * 1024**2
    maximum_inodes: int = 1000000

    def __post_init__(self):
        root = Path(self.root)
        if not root.is_absolute() or ".." in root.parts or str(root).startswith("//"):
            raise ValueError("hard_storage_absolute_root_required")
        object.__setattr__(self, "root", root)
        _integer(self.maximum_bytes, 16 * 1024**2, 2**41)
        _integer(self.reserve_bytes, 0, self.maximum_bytes)
        _integer(self.maximum_inodes, 128, 100000000)


class HardStorage:
    def __init__(self, policy: VolumePolicy):
        if type(policy) is not VolumePolicy:
            raise ValueError("hard_storage_policy_required")
        self.policy = policy
        self._identity = None

    def inspect(self):
        if sys.platform != "linux":
            raise StorageUnavailable("hard_storage_requires_native_linux")
        root = self.policy.root
        _no_links(root)
        if not root.is_dir() or not root.is_mount() or root == Path("/"):
            raise StorageUnavailable("hard_storage_dedicated_mount_required")
        raw = Path("/proc/self/mountinfo").read_bytes()
        if len(raw) > 2 * 1024**2:
            raise StorageUnavailable("hard_storage_mountinfo_over_limit")
        matches = []
        for line in raw.decode("utf-8", errors="strict").splitlines():
            before, separator, after = line.partition(" - ")
            fields, filesystem = before.split(), after.split()
            if separator and len(fields) >= 6 and len(filesystem) >= 3 and _unescape(fields[4]) == str(root):
                matches.append((fields, filesystem))
        if len(matches) != 1:
            raise StorageUnavailable("hard_storage_exact_mount_ambiguous")
        fields, filesystem = matches[0]
        options = set(fields[5].split(","))
        if (_unescape(fields[3]) != "/" or filesystem[0] not in ("ext4", "xfs")
                or not {"rw", "nodev", "nosuid"}.issubset(options) or "ro" in options):
            raise StorageUnavailable("hard_storage_mount_policy_mismatch")
        if not re.fullmatch(r"[0-9]+:[0-9]+", fields[2]):
            raise StorageUnavailable("hard_storage_block_identity_invalid")
        info = root.stat()
        # Binding the system filesystem at a different mountpoint does not make
        # it a dedicated capped deployment volume, even if its total size fits.
        if info.st_dev == Path("/").stat().st_dev:
            raise StorageUnavailable("hard_storage_system_volume_denied")
        major, minor = map(int, fields[2].split(":"))
        if info.st_dev != os.makedev(major, minor):
            raise StorageUnavailable("hard_storage_kernel_device_mismatch")
        block = Path("/sys/dev/block") / fields[2]
        # sysfs block metadata is kernel-owned. Bind capacity to this exact
        # mounted device, not a configured image filename or mount source text.
        try:
            sectors = (block / "size").read_text().strip()
            if not re.fullmatch(r"[0-9]{1,20}", sectors):
                raise ValueError("invalid_sectors")
            device_bytes = int(sectors) * 512
        except (OSError, ValueError) as exc:
            raise StorageUnavailable("hard_storage_block_capacity_unavailable") from exc
        stats = os.statvfs(root)
        filesystem_bytes = stats.f_blocks * stats.f_frsize
        if (device_bytes <= 0 or device_bytes > self.policy.maximum_bytes
                or filesystem_bytes <= 0 or filesystem_bytes > device_bytes
                or stats.f_files <= 0 or stats.f_files > self.policy.maximum_inodes):
            raise StorageUnavailable("hard_storage_capacity_exceeds_policy")
        identity = (info.st_dev, info.st_ino, device_bytes)
        if self._identity is not None and identity != self._identity:
            raise StorageUnavailable("hard_storage_mount_identity_changed")
        self._identity = identity
        free = stats.f_bavail * stats.f_frsize
        if free < self.policy.reserve_bytes:
            raise StorageUnavailable("hard_storage_low_free_space")
        return {"verification": "kernel_bounded_block_filesystem", "filesystem": filesystem[0],
                "device": fields[2], "device_bytes": device_bytes,
                "filesystem_bytes": filesystem_bytes, "available_bytes": free,
                "maximum_bytes": self.policy.maximum_bytes, "inode_capacity": stats.f_files,
                "reserve_bytes": self.policy.reserve_bytes}

    def check(self, *paths):
        report = self.inspect()
        root = self.policy.root
        for raw in paths:
            path = Path(raw)
            _no_links(path)
            if (not path.is_absolute() or ".." in path.parts or not path.is_relative_to(root)):
                raise StorageUnavailable("hard_storage_path_outside_volume")
            existing = path
            while not existing.exists():
                existing = existing.parent
            if existing.stat().st_dev != self._identity[0]:
                raise StorageUnavailable("hard_storage_nested_device_denied")
        return report
