"""Reviewable external netns plan; this module executes NO Docker/firewall action.

The fixed sidecar owns its own network namespace. An unprivileged user container
may join it only AFTER the controller verifies the owned network, immutable
sidecar image, exact capabilities and installed firewall readiness. No host
network namespace, privileged container or host firewall mutation is supported.
Runtime verification remains blocked until explicit approval of isolated tests.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import re

from .protocol import identifier, bounded_integer


_PRIVATE = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_IMAGE = re.compile(r"sha256:[a-f0-9]{64}")
_DOCKER_ID = re.compile(r"[a-f0-9]{64}")
_ENTRYPOINT = "/usr/local/bin/citrus-netns"
_PATH_ENV = "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_RUN_TMPFS = "rw,noexec,nosuid,nodev,size=1048576,nr_inodes=64,mode=0700"


def _same_json(actual, expected):
    """Unlike Python equality, inspection comparisons must distinguish bool/int."""
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return actual.keys() == expected.keys() and all(
            _same_json(actual[key], value) for key, value in expected.items())
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            _same_json(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _fields_match(document, expected):
    return type(document) is dict and all(
        key in document and _same_json(document[key], value) for key, value in expected.items())


def _empty_fields(document, names, empty):
    # Docker versions use null or omit unused optional fields. Other falsey
    # values (0, false, "") are malformed, rather than evidence of an empty list.
    return all(document.get(name) is None or _same_json(document[name], empty) for name in names)


def _docker_id(value):
    return isinstance(value, str) and _DOCKER_ID.fullmatch(value) is not None


@dataclass(frozen=True)
class NamespacePlan:
    execution_id: str
    fence: int
    image_id: str
    subnet: str
    client_ip: str
    gateway_ip: str
    gateway_port: int = 8080

    def __post_init__(self):
        identifier(self.execution_id)
        bounded_integer(self.fence, 1, 2**31 - 1, "fence")
        if not isinstance(self.image_id, str) or not _IMAGE.fullmatch(self.image_id):
            raise ValueError("immutable_namespace_image_required")
        if not isinstance(self.subnet, str):
            raise ValueError("private_bounded_namespace_subnet_required")
        network = ipaddress.ip_network(self.subnet, strict=True)
        if (network.version != 4 or not 24 <= network.prefixlen <= 29
                or str(network) != self.subnet
                or not any(network.subnet_of(p) for p in _PRIVATE)):
            raise ValueError("private_bounded_namespace_subnet_required")
        if any(not isinstance(value, str) for value in (self.client_ip, self.gateway_ip)):
            raise ValueError("distinct_owned_namespace_addresses_required")
        addresses = [ipaddress.IPv4Address(value) for value in (self.client_ip, self.gateway_ip)]
        if (addresses[0] == addresses[1]
                or any(str(ip) != value for ip, value in zip(addresses, (self.client_ip, self.gateway_ip)))
                or any(ip not in network or ip in (network.network_address, network.broadcast_address,
                                                  network.network_address + 1) for ip in addresses)):
            raise ValueError("distinct_owned_namespace_addresses_required")
        bounded_integer(self.gateway_port, 1024, 65535, "gateway_port")

    @property
    def bridge_ip(self):
        # Reserve Docker's bridge address separately from the HTTPS gateway.
        return str(ipaddress.ip_network(self.subnet).network_address + 1)

    @property
    def network_name(self):
        return "citrus-egress-" + self.execution_id + "-" + str(self.fence)

    @property
    def sidecar_name(self):
        return "citrus-netns-" + self.execution_id + "-" + str(self.fence)

    @property
    def labels(self):
        return {"org.citrusscholar.egress.execution": self.execution_id,
                "org.citrusscholar.egress.fence": str(self.fence)}

    @property
    def ipv4_rules(self):
        return ("*filter\n:INPUT DROP [0:0]\n:FORWARD DROP [0:0]\n:OUTPUT DROP [0:0]\n"
                "-A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT\n"
                "-A OUTPUT -d " + self.gateway_ip + "/32 -p tcp --dport " + str(self.gateway_port)
                + " -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT\nCOMMIT\n")

    @property
    def ipv6_rules(self):
        return "*filter\n:INPUT DROP [0:0]\n:FORWARD DROP [0:0]\n:OUTPUT DROP [0:0]\nCOMMIT\n"

    @property
    def rule_hash(self):
        return hashlib.sha256((self.ipv4_rules + self.ipv6_rules).encode("ascii")).hexdigest()

    @property
    def readiness(self):
        return {"execution_id": self.execution_id, "fence": self.fence, "rule_hash": self.rule_hash}

    @property
    def _arguments(self):
        return [self.gateway_ip, str(self.gateway_port), self.execution_id, str(self.fence), self.rule_hash]

    def network_command(self):
        command = ["network", "create", "--driver=bridge", "--internal", "--ipv6=false",
                   "--subnet=" + self.subnet, "--gateway=" + self.bridge_ip]
        for key, value in sorted(self.labels.items()):
            command += ["--label", key + "=" + value]
        return [*command, self.network_name]

    def sidecar_command(self):
        command = ["create", "--name", self.sidecar_name, "--pull=never", "--restart=no",
            "--network=" + self.network_name, "--ip=" + self.client_ip, "--user=0:0",
            "--env=" + _PATH_ENV, "--workdir=/", "--read-only", "--cap-drop=ALL", "--cap-add=NET_ADMIN", "--security-opt=no-new-privileges:true",
            "--pids-limit=16", "--memory=64m", "--memory-swap=64m", "--cpus=0.1", "--runtime=runc",
            "--ipc=none", "--cgroupns=private", "--ulimit=core=0:0", "--no-healthcheck",
            "--log-driver=json-file", "--log-opt=max-size=1m", "--log-opt=max-file=1",
            "--tmpfs", "/run:" + _RUN_TMPFS, "--entrypoint=" + _ENTRYPOINT]
        for key, value in sorted(self.labels.items()):
            command += ["--label", key + "=" + value]
        return [*command, self.image_id, *self._arguments]

    def verify_network(self, document):
        error = "owned_internal_network_required"
        if not _fields_match(document, {
                "Name": self.network_name, "Driver": "bridge", "Scope": "local",
                "Internal": True, "EnableIPv6": False, "Attachable": False, "Ingress": False,
                "Labels": self.labels, "Options": {}}):
            raise PermissionError(error)
        ipam = document.get("IPAM")
        if (not _docker_id(document.get("Id"))
                or not _fields_match(ipam, {"Driver": "default", "Config": [
                    {"Subnet": self.subnet, "Gateway": self.bridge_ip}]})
                or not _empty_fields(ipam, ("Options",), {})
                or document.get("EnableIPv4", True) is not True
                or document.get("ConfigOnly", False) is not False
                or not _same_json(document.get("ConfigFrom", {"Network": ""}), {"Network": ""})):
            raise PermissionError(error)
        endpoints = document.get("Containers")
        if type(endpoints) is not dict or len(endpoints) > 2:
            raise PermissionError(error)
        seen = set()
        prefix = self.subnet.split("/")[1]
        for container_id, endpoint in endpoints.items():
            if (not _docker_id(container_id) or type(endpoint) is not dict
                    or not isinstance(endpoint.get("Name"), str) or not endpoint["Name"]
                    or endpoint.get("IPv6Address") != ""):
                raise PermissionError(error)
            address = endpoint.get("IPv4Address")
            if (not isinstance(address, str) or address in seen
                    or address not in (self.client_ip + "/" + prefix, self.gateway_ip + "/" + prefix)
                    or (address == self.client_ip + "/" + prefix and endpoint["Name"] != self.sidecar_name)):
                raise PermissionError(error)
            seen.add(address)

    def verify_ready(self, document, readiness):
        error = "verified_namespace_firewall_required"
        if (not _fields_match(document, {"Name": "/" + self.sidecar_name,
                "Image": self.image_id, "Path": _ENTRYPOINT, "Args": self._arguments})
                or not _same_json(readiness, self.readiness)):
            raise PermissionError(error)
        host, config, state = (document.get(key) for key in ("HostConfig", "Config", "State"))
        if (not _fields_match(config, {"Image": self.image_id, "Labels": self.labels,
                "User": "0:0", "Entrypoint": [_ENTRYPOINT], "Cmd": self._arguments,
                "Env": [_PATH_ENV], "WorkingDir": "/", "Healthcheck": {"Test": ["NONE"]}})
                or not _empty_fields(config, ("Volumes", "ExposedPorts"), {})
                or not _fields_match(state, {"Status": "running", "Running": True,
                    "Paused": False, "Restarting": False, "Dead": False, "OOMKilled": False,
                    "Error": ""})
                or type(state.get("Pid")) is not int or state["Pid"] <= 0):
            raise PermissionError(error)
        if (not _fields_match(host, {
                "NetworkMode": self.network_name, "Privileged": False, "ReadonlyRootfs": True,
                "CapAdd": ["NET_ADMIN"], "CapDrop": ["ALL"], "Runtime": "runc",
                "IpcMode": "none", "CgroupnsMode": "private", "PublishAllPorts": False,
                "PidsLimit": 16, "Memory": 64 * 1024**2, "MemorySwap": 64 * 1024**2,
                "NanoCpus": 100_000_000, "Tmpfs": {"/run": _RUN_TMPFS},
                "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
                "Ulimits": [{"Name": "core", "Soft": 0, "Hard": 0}],
                "LogConfig": {"Type": "json-file", "Config": {"max-size": "1m", "max-file": "1"}}})
                or not _empty_fields(host, ("Binds", "Mounts", "VolumesFrom", "Devices", "DeviceRequests",
                    "DeviceCgroupRules", "Links", "ExtraHosts", "Dns", "DnsOptions", "DnsSearch", "GroupAdd"), [])
                or not _empty_fields(host, ("Sysctls", "PortBindings", "StorageOpt"), {})
                or any(host.get(key, "") != "" for key in
                    ("PidMode", "UTSMode", "UsernsMode", "Cgroup", "CgroupParent", "Isolation"))
                or not any(_same_json(host.get("SecurityOpt"), [option]) for option in
                    ("no-new-privileges:true", "no-new-privileges"))):
            raise PermissionError(error)
        settings = document.get("NetworkSettings")
        if type(settings) is not dict or not _empty_fields(settings, ("Ports",), {}):
            raise PermissionError(error)
        networks = settings.get("Networks")
        if type(networks) is not dict or set(networks) != {self.network_name}:
            raise PermissionError(error)
        endpoint = networks[self.network_name]
        if (not _fields_match(endpoint, {"IPAddress": self.client_ip,
                "IPPrefixLen": int(self.subnet.split("/")[1]), "Gateway": "",
                "GlobalIPv6Address": "", "GlobalIPv6PrefixLen": 0, "IPv6Gateway": ""})
                or not _docker_id(endpoint.get("NetworkID")) or not _docker_id(endpoint.get("EndpointID"))
                or not _empty_fields(endpoint, ("Links",), [])
                or not _empty_fields(endpoint, ("DriverOpts",), {})):
            raise PermissionError(error)
        ipam = endpoint.get("IPAMConfig")
        if (type(ipam) is not dict or ipam.get("IPv4Address") != self.client_ip
                or set(ipam) - {"IPv4Address", "IPv6Address", "LinkLocalIPs"}
                or ipam.get("IPv6Address", "") != ""
                or not _empty_fields(ipam, ("LinkLocalIPs",), [])):
            raise PermissionError(error)
        mounts = document.get("Mounts")
        # Legacy --tmpfs may appear only in HostConfig.Tmpfs. If Mounts includes
        # it, reject duplicates, alternate destinations, bind/volume sources.
        if (type(mounts) is not list or len(mounts) > 1
                or any(not _fields_match(mount, {"Type": "tmpfs", "Destination": "/run", "RW": True})
                    or mount.get("Source", "") != "" or mount.get("Propagation", "") != ""
                    or mount.get("Mode", "") != "" for mount in mounts)):
            raise PermissionError(error)
        container_id = document.get("Id")
        if not _docker_id(container_id):
            raise PermissionError("verified_namespace_container_id_required")
        # Exact ID avoids a container-name replacement race when joining. The
        # caller must obtain fresh inspection/readiness from the trusted daemon;
        # matching this JSON does not itself attest that firewall rules exist.
        return "container:" + container_id
