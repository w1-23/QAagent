"""Offline generation of a reviewable gateway bundle and Docker command plan.

No commands run here. This is NOT execution admission. Before use, the controller
must pass runtime/TLS/kernel and storage quota gates, verify the owned networks,
prepare private control directories, fence the execution and revoke networking on
cancel. Gateway private CA and ledger mounts must never enter the user container.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from .egress_namespace import NamespacePlan
from .egress_policy import EgressPolicy
from .oci import safe_linux_path

_IMAGE = re.compile(r"sha256:[a-f0-9]{64}")
_NETWORK = re.compile(r"citrus-egress-upstream-[a-z0-9][a-z0-9-]{0,47}")
_ID = re.compile(r"[a-f0-9]{64}")

ADDON = '''"""Host-owned, immutable addon entrypoint. No execution policy from clients."""
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, "/opt/citrus_gateway")
from citrus_egress.egress_gateway import MitmproxyGateway
addons = [MitmproxyGateway()]
'''


def write_gateway_bundle(destination: Path) -> dict:
    """Create a NEW source-only directory; no environment, database or secrets."""
    destination = Path(destination)
    if not destination.is_absolute():
        raise ValueError("absolute_new_gateway_bundle_required")
    for part in (destination, *destination.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise PermissionError("linked_gateway_bundle_denied")
    destination.mkdir(mode=0o755, exist_ok=False)
    destination.chmod(0o755)  # Source only; non-root gateway must be able to read.
    package = destination / "citrus_egress"
    package.mkdir(mode=0o755)
    package.chmod(0o755)
    entries = {"addon.py": ADDON.encode(), "citrus_egress/__init__.py": b""}
    for name in ("egress_gateway.py", "egress_policy.py"):
        entries["citrus_egress/" + name] = Path(__file__).with_name(name).read_bytes()
    manifest = {"schema": "citrus.gateway.bundle/1", "files": {}}
    for name, data in sorted(entries.items()):
        (destination / name).write_bytes(data)
        (destination / name).chmod(0o644)
        manifest["files"][name] = {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
    (destination / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
    (destination / "manifest.json").chmod(0o644)
    return manifest


@dataclass(frozen=True)
class GatewayLaunchPlan:
    namespace: NamespacePlan
    policy: EgressPolicy
    image_id: str
    source_root: str
    policy_file: str
    private_ca_root: str
    private_ledger_root: str
    upstream_network: str

    def __post_init__(self):
        if type(self.namespace) is not NamespacePlan or type(self.policy) is not EgressPolicy:
            raise ValueError("host_owned_gateway_plan_required")
        if self.policy.mode != "research_https" or self.namespace.execution_id != self.policy.execution_id:
            raise ValueError("gateway_execution_policy_mismatch")
        if not isinstance(self.image_id, str) or not _IMAGE.fullmatch(self.image_id):
            raise ValueError("immutable_gateway_image_required")
        if not isinstance(self.upstream_network, str) or not _NETWORK.fullmatch(self.upstream_network):
            raise ValueError("owned_gateway_upstream_network_required")
        paths = [getattr(self, name) for name in ("source_root", "policy_file", "private_ca_root", "private_ledger_root")]
        for path in paths:
            safe_linux_path(path)
        for i, path in enumerate(paths):
            if any(path == other or path.startswith(other + "/") or other.startswith(path + "/") for other in paths[i + 1:]):
                raise ValueError("disjoint_gateway_control_mounts_required")

    @property
    def name(self):
        return "citrus-gateway-" + self.namespace.execution_id + "-" + str(self.namespace.fence)

    def create_command(self):
        n = self.namespace
        command = ["create", "--name", self.name, "--pull=never", "--restart=no", "--read-only",
            "--network=" + n.network_name, "--ip=" + n.gateway_ip, "--user=65534:65534",
            "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--ipc=none", "--cgroupns=private",
            "--pids-limit=64", "--memory=256m", "--memory-swap=256m", "--cpus=0.5",
            "--ulimit=core=0:0", "--no-healthcheck", "--log-driver=none",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=8388608,nr_inodes=256,mode=1777"]
        for key, value in sorted({**n.labels, "org.citrusscholar.egress.policy": self.policy.fingerprint}.items()):
            command += ["--label", key + "=" + value]
        for source, target, readonly in ((self.source_root, "/opt/citrus_gateway", True),
                (self.policy_file, "/policy/execution.json", True),
                (self.private_ca_root, "/control/ca", False),
                (self.private_ledger_root, "/control/ledger", False)):
            command += ["--mount", "type=bind,src=" + source + ",dst=" + target
                        + (",readonly" if readonly else "") + ",bind-recursive=disabled"]
        command += ["--entrypoint=/usr/local/bin/mitmdump", self.image_id,
            "--quiet", "--listen-host", n.gateway_ip, "--listen-port", str(n.gateway_port),
            "--mode", "regular", "--scripts", "/opt/citrus_gateway/addon.py"]
        settings = {"connection_strategy": "lazy", "ssl_insecure": "false", "http2": "false", "http3": "false",
            "websocket": "false", "rawtcp": "false", "upstream_cert": "false", "body_size_limit": "1048576",
            "confdir": "/control/ca", "citrus_policy_file": "/policy/execution.json", "citrus_client_ip": n.client_ip,
            "citrus_receipt_file": "/control/ledger/responses.jsonl"}
        # This private CA directory must initially contain only provisioned CA
        # material, never config.yaml or additional mitmproxy addon scripts.
        for key, value in sorted(settings.items()):
            command += ["--set", key + "=" + value]
        return command

    def connect_upstream_command(self, verified_gateway_id: str):
        if not isinstance(verified_gateway_id, str) or not _ID.fullmatch(verified_gateway_id):
            raise ValueError("verified_gateway_container_id_required")
        return ["network", "connect", self.upstream_network, verified_gateway_id]

    def public_client_environment(self):
        """Compatibility hints ONLY; a verified external firewall is mandatory."""
        proxy = "http://" + self.namespace.gateway_ip + ":" + str(self.namespace.gateway_port)
        ca = "/etc/citrus-egress/ca.pem"
        return {"HTTPS_PROXY": proxy, "https_proxy": proxy, "NO_PROXY": "", "no_proxy": "",
                "REQUESTS_CA_BUNDLE": ca, "SSL_CERT_FILE": ca, "CURL_CA_BUNDLE": ca,
                "WGETRC": "/etc/citrus-egress/wgetrc"}

    def public_client_configuration(self):
        """Public config only; the controller separately exports the public CA."""
        return {"wgetrc": ("check_certificate = on\nca_certificate = /etc/citrus-egress/ca.pem\n"
                           "use_proxy = on\nhttps_proxy = "
                           + self.public_client_environment()["HTTPS_PROXY"] + "\n")}
