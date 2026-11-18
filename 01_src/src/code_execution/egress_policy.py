"""Pure host-owned policy groundwork; this module performs NO network enforcement.

Only a trusted host may construct/parse policies. Never accept a model-supplied
policy, identity, DNS answer set, peer claim or accounting event as authority.
``authorize_resolved`` does not resolve DNS: the gateway supplies ALL A/AAAA
answers and must connect to the returned exact peer without resolving again.
Every redirect/new connection needs fresh authorization. A serialized binding is
an audit record, not a signed capability or evidence of the socket peer.

A future maintained TLS-terminating gateway still owns authenticated execution
routing, trusted DNS (including CNAME/search/TTL handling), socket peer pinning,
TLS certificate/SNI/Host matching, HTTP parsing/header limits, credential/header
filtering, deadlines, concurrency serialization, counting bytes before forwarding
(including decompressed content), and invoking accounting on every terminal path.
It must disallow CONNECT, upgrades, opaque tunnels and alternate protocols, and
recognize any deployment-specific translation prefixes. The kernel/container
boundary must prohibit direct traffic, alternate resolvers and gateway bypass.
POST 'read_only' is an operator assertion, not proof of remote server semantics.
Nothing here enables research_https in OCI or changes offline execution defaults.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit, urlunsplit


class PolicyDenied(ValueError):
    """Invalid policy, destination or stream transition; fail closed."""


_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}")
_ID = re.compile(r"[a-f0-9]{32}")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_V4_DENY = tuple(ipaddress.ip_network(n) for n in (
    "192.0.0.0/24", "192.88.99.0/24", "198.18.0.0/15",
    "168.63.129.16/32",  # Azure host virtual/platform endpoint.
))
_V6_DENY = tuple(ipaddress.ip_network(n) for n in (
    "::/96", "::ffff:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48",
    "2001::/23", "2002::/16", "3ffe::/16", "3fff::/20",
))
_V6_UNICAST = ipaddress.ip_network("2000::/3")


def _deny(message: str):
    raise PolicyDenied(message)


def _integer(value, low: int, high: int, name: str) -> int:
    if type(value) is not int or not low <= value <= high:
        _deny("invalid_" + name)
    return value


def _identity(task_id, execution_id):
    if type(task_id) is not str or not _TOKEN.fullmatch(task_id):
        _deny("invalid_task_id")
    if type(execution_id) is not str or not _ID.fullmatch(execution_id):
        _deny("invalid_execution_id")


def _text(value, limit: int, name: str):
    if type(value) is not str or not 1 <= len(value) <= limit:
        _deny("invalid_" + name)
    if any(c.isspace() or ord(c) <= 32 or 127 <= ord(c) <= 159 for c in value):
        _deny("invalid_" + name)


def public_ip(value: str) -> str:
    """Return canonical, conservatively public IP; no DNS or reachability claim."""
    _text(value, 64, "ip")
    if "%" in value:
        _deny("scoped_ip_denied")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise PolicyDenied("invalid_ip") from exc
    if isinstance(address, ipaddress.IPv4Address) and str(address) != value:
        _deny("ambiguous_ipv4")
    if (not address.is_global or address.is_private or address.is_loopback
            or address.is_link_local or address.is_reserved or address.is_multicast
            or address.is_unspecified):
        _deny("non_public_ip")
    denied = _V4_DENY if address.version == 4 else _V6_DENY
    if any(address in network for network in denied):
        _deny("special_ip_denied")
    if address.version == 6 and address not in _V6_UNICAST:
        _deny("non_unicast_ipv6")
    return str(address)


def normalize_host(value: str) -> str:
    """Canonical exact host, with conservative IDNA and no wildcard semantics."""
    _text(value, 253, "host")
    if any(c in value for c in "%/@\\?#[]") or value.endswith("."):
        _deny("ambiguous_host")
    if ":" in value:
        return public_ip(value)
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        pass
    else:
        return public_ip(value)
    # Do not let DNS/libc reinterpret numeric, short, octal or hex IPv4 forms.
    if any(c in value for c in "。．｡"):
        _deny("ambiguous_idna_separator")
    try:
        host = value.lower().encode("idna").decode("ascii")
        decoded = host.encode("ascii").decode("idna")
        if value.lower() not in (host, decoded):
            _deny("lossy_idna")
        if decoded.encode("idna").decode("ascii") != host:
            _deny("invalid_idna_roundtrip")
    except (UnicodeError, ValueError) as exc:
        raise PolicyDenied("invalid_idna") from exc
    labels = host.split(".")
    if (len(host) > 253 or len(labels) < 2 or any(not _LABEL.fullmatch(x) for x in labels)
            or not re.fullmatch(r"[a-z][a-z0-9-]*", labels[-1])
            or all(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", x) for x in labels)):
        _deny("ambiguous_dns_host")
    return host


@dataclass(frozen=True)
class Destination:
    url: str
    host: str
    port: int = 443


def normalize_https_url(url: str) -> Destination:
    """Syntax normalization ONLY. This is not destination authorization."""
    _text(url, 8192, "url")
    if "\\" in url or "#" in url or any(0xD800 <= ord(c) <= 0xDFFF for c in url):
        _deny("ambiguous_url")
    try:
        parts = urlsplit(url)
        authority = parts.netloc
        if parts.scheme != "https" or not authority or "@" in authority:
            _deny("https_authority_required")
        if authority.startswith("["):
            end = authority.find("]")
            if end < 0 or authority[end + 1:] not in ("", ":443"):
                _deny("invalid_ipv6_authority")
            raw_host = authority[1:end]
            if ":" not in raw_host:
                _deny("ipv6_literal_required")
        else:
            if authority.count(":") > 1:
                _deny("bracket_ipv6_required")
            raw_host, separator, port = authority.partition(":")
            if separator and port != "443":
                _deny("https_port_443_required")
        host = normalize_host(raw_host)
    except ValueError as exc:
        raise PolicyDenied("invalid_https_url") from exc
    canonical_authority = "[" + host + "]" if ":" in host else host
    normalized = urlunsplit(("https", canonical_authority, parts.path or "/", parts.query, ""))
    return Destination(normalized, host)


@dataclass(frozen=True)
class EgressLimits:
    max_requests: int = 32
    max_response_bytes: int = 8 * 1024**2
    max_total_response_bytes: int = 32 * 1024**2
    max_total_request_bytes: int = 1024**2
    max_redirects_per_chain: int = 3
    max_total_redirects: int = 8

    def __post_init__(self):
        for name, low, high in (
            ("max_requests", 1, 256), ("max_response_bytes", 1, 64 * 1024**2),
            ("max_total_response_bytes", 1, 256 * 1024**2),
            ("max_total_request_bytes", 0, 8 * 1024**2),
            ("max_redirects_per_chain", 0, 10), ("max_total_redirects", 0, 64),
        ):
            _integer(getattr(self, name), low, high, name)
        if self.max_response_bytes > self.max_total_response_bytes:
            _deny("response_limit_exceeds_total")
        if self.max_redirects_per_chain > self.max_total_redirects:
            _deny("redirect_limit_exceeds_total")


@dataclass(frozen=True)
class ReadOnlyPostRule:
    host: str
    max_request_bytes: int
    method: str = "POST"
    read_only: bool = True

    def __post_init__(self):
        if normalize_host(self.host) != self.host:
            _deny("post_host_must_be_canonical")
        _integer(self.max_request_bytes, 1, 1024**2, "post_bytes")
        if self.method != "POST" or self.read_only is not True:
            _deny("explicit_read_only_post_required")


@dataclass(frozen=True)
class EgressPolicy:
    task_id: str
    execution_id: str
    mode: str = "offline"
    scope: str = "public"
    allowed_hosts: tuple[str, ...] = ()
    methods: tuple[str, ...] = ("GET", "HEAD")
    post_rules: tuple[ReadOnlyPostRule, ...] = ()
    limits: EgressLimits = EgressLimits()
    version: str = "citrus.egress/1"

    def __post_init__(self):
        _identity(self.task_id, self.execution_id)
        if self.version != "citrus.egress/1" or self.mode not in ("offline", "research_https"):
            _deny("invalid_egress_mode_or_version")
        if self.scope not in ("public", "sensitive"):
            _deny("invalid_egress_scope")
        if type(self.limits) is not EgressLimits:
            _deny("invalid_egress_limits")
        if type(self.allowed_hosts) is not tuple or len(self.allowed_hosts) > 64:
            _deny("invalid_allowlist")
        for host in self.allowed_hosts:
            if normalize_host(host) != host:
                _deny("allowlist_host_must_be_canonical")
        if len(set(self.allowed_hosts)) != len(self.allowed_hosts):
            _deny("duplicate_allowlist_host")
        if (type(self.methods) is not tuple or not 1 <= len(self.methods) <= 2
                or any(type(m) is not str or m not in ("GET", "HEAD") for m in self.methods)
                or len(set(self.methods)) != len(self.methods)):
            _deny("invalid_read_methods")
        if type(self.post_rules) is not tuple or len(self.post_rules) > 32:
            _deny("invalid_post_rules")
        if any(type(r) is not ReadOnlyPostRule for r in self.post_rules):
            _deny("invalid_post_rule")
        if len({r.host for r in self.post_rules}) != len(self.post_rules):
            _deny("duplicate_post_rule")
        for rule in self.post_rules:
            if self.allowed_hosts and rule.host not in self.allowed_hosts:
                _deny("post_outside_allowlist")
            if rule.max_request_bytes > self.limits.max_total_request_bytes:
                _deny("post_limit_exceeds_total")
        if self.mode == "research_https" and self.scope == "sensitive" and not self.allowed_hosts:
            _deny("sensitive_requires_offline_or_allowlist")

    def as_dict(self) -> dict:
        # JSON roundtrip also turns immutable tuples into JSON arrays.
        return json.loads(self.serialize())

    def serialize(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.serialize().encode("utf-8")).hexdigest()

    @classmethod
    def parse(cls, payload: str) -> EgressPolicy:
        if type(payload) is not str or len(payload) > 32768:
            _deny("policy_json_limit")
        try:
            if len(payload.encode("utf-8")) > 32768:
                _deny("policy_json_limit")
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        _deny("duplicate_policy_json_key")
                    result[key] = value
                return result
            raw = json.loads(payload, object_pairs_hook=unique,
                             parse_constant=lambda _: _deny("nonfinite_policy_json"))
            _exact_fields(raw, cls)
            _exact_fields(raw["limits"], EgressLimits)
            for name in ("allowed_hosts", "methods", "post_rules"):
                if type(raw[name]) is not list:
                    _deny("policy_array_required")
            if len(raw["allowed_hosts"]) > 64 or len(raw["post_rules"]) > 32:
                _deny("policy_array_limit")
            for rule in raw["post_rules"]:
                _exact_fields(rule, ReadOnlyPostRule)
            raw["limits"] = EgressLimits(**raw["limits"])
            raw["post_rules"] = tuple(ReadOnlyPostRule(**r) for r in raw["post_rules"])
            raw["allowed_hosts"] = tuple(raw["allowed_hosts"])
            raw["methods"] = tuple(raw["methods"])
            return cls(**raw)
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise PolicyDenied("invalid_policy_json") from exc


def _exact_fields(value, cls):
    if type(value) is not dict or set(value) != {f.name for f in fields(cls)}:
        _deny("invalid_policy_fields")


@dataclass(frozen=True)
class ResolvedAuthorization:
    task_id: str
    execution_id: str
    policy_fingerprint: str
    destination: Destination
    method: str
    resolved_ips: tuple[str, ...]
    selected_peer: str
    max_request_bytes: int

    def as_dict(self) -> dict:
        return asdict(self)


def authorize_resolved(policy: EgressPolicy, *, task_id: str, execution_id: str,
                       url: str, method: str, resolved_ips: tuple[str, ...],
                       selected_peer: str) -> ResolvedAuthorization:
    """Bind supplied DNS answers and exact peer; NEVER resolve/connect implicitly.

    Reject the entire set if any A/AAAA is unsafe. The trusted resolver must
    supply the complete set, not just a favorable answer. Reauthorize on retry,
    new connection and redirect. Gateway must independently verify getpeername.
    """
    if type(policy) is not EgressPolicy:
        _deny("host_policy_required")
    _identity(task_id, execution_id)
    if (task_id, execution_id) != (policy.task_id, policy.execution_id):
        _deny("egress_identity_mismatch")
    if policy.mode == "offline":
        _deny("egress_offline")
    destination = normalize_https_url(url)
    if policy.allowed_hosts and destination.host not in policy.allowed_hosts:
        _deny("host_not_allowed")
    request_cap = 0
    if type(method) is not str:
        _deny("invalid_method")
    if method not in policy.methods:
        rule = next((r for r in policy.post_rules if r.host == destination.host and r.method == method), None)
        if rule is None:
            _deny("method_not_allowed")
        request_cap = rule.max_request_bytes
    if type(resolved_ips) is not tuple or not 1 <= len(resolved_ips) <= 32:
        _deny("bounded_dns_answers_required")
    addresses = tuple(public_ip(ip) for ip in resolved_ips)
    if len(set(addresses)) != len(addresses):
        _deny("duplicate_dns_answer")
    peer = public_ip(selected_peer)
    if peer not in addresses:
        _deny("peer_not_in_dns_answers")
    try:
        literal = ipaddress.ip_address(destination.host)
    except ValueError:
        pass
    else:
        if addresses != (str(literal),) or peer != str(literal):
            _deny("literal_peer_mismatch")
    return ResolvedAuthorization(task_id, execution_id, policy.fingerprint, destination,
                                 method, addresses, peer, request_cap)


@dataclass(frozen=True)
class ResponseReceipt:
    request_number: int
    status_code: int
    method: str
    redirects_in_chain: int
    request_bytes: int
    response_bytes: int
    complete: bool = True


class StreamBudget:
    """Trusted, sequential accounting. No thread safety, persistence or I/O.

    Count actual chunks BEFORE forwarding them; Content-Length is never input
    to this API. Count encoded and decoded data through the same budget if both
    forms are processed (conservative double counting is acceptable). A denied
    transition, timeout, cancellation or overflow permanently closes the budget.
    A redirect requires a fresh authorization and the exact previous receipt;
    abandoning a redirect requires explicit discard_redirect().
    """

    def __init__(self, policy: EgressPolicy):
        if type(policy) is not EgressPolicy:
            _deny("host_policy_required")
        self._policy = policy
        self._active = None
        self._last = None
        self._status = None
        self._chain = self._request_bytes = self._response_bytes = 0
        self._requests = self._redirects = 0
        self._total_request_bytes = self._total_response_bytes = 0
        self._closed = self._incomplete = False
        self._reason = None

    def _fail(self, reason):
        self._closed = self._incomplete = True
        self._reason = reason
        _deny(reason)

    def _open(self):
        if self._closed:
            _deny("budget_closed")

    def start_request(self, authorization: ResolvedAuthorization, *,
                      redirect_from: ResponseReceipt | None = None):
        self._open()
        if self._active is not None:
            self._fail("request_already_active")
        if type(authorization) is not ResolvedAuthorization:
            self._fail("authorization_required")
        try:
            checked = authorize_resolved(self._policy, task_id=authorization.task_id,
                execution_id=authorization.execution_id, url=authorization.destination.url,
                method=authorization.method, resolved_ips=authorization.resolved_ips,
                selected_peer=authorization.selected_peer)
            if checked != authorization:
                self._fail("authorization_binding_mismatch")
        except (ValueError, AttributeError, TypeError):
            self._fail("invalid_authorization")
        pending = self._last is not None and self._last.status_code in _REDIRECTS
        if redirect_from is not None:
            if not pending or redirect_from is not self._last:
                self._fail("invalid_redirect_receipt")
            previous = self._last
            # No implicit POST-to-GET rewrite; 303 is the explicit exception.
            expected = ("HEAD" if previous.method == "HEAD" else "GET") if previous.status_code == 303 else previous.method
            if authorization.method != expected:
                self._fail("invalid_redirect_method")
            chain = previous.redirects_in_chain + 1
            if (chain > self._policy.limits.max_redirects_per_chain
                    or self._redirects >= self._policy.limits.max_total_redirects):
                self._fail("redirect_limit")
        elif pending:
            self._fail("redirect_disposition_required")
        else:
            chain = 0
        if self._requests >= self._policy.limits.max_requests:
            self._fail("request_limit")
        self._requests += 1
        self._redirects += int(redirect_from is not None)
        self._chain = chain
        self._active = authorization
        self._status = self._last = None
        self._request_bytes = self._response_bytes = 0

    def discard_redirect(self):
        self._open()
        if self._active is not None or self._last is None or self._last.status_code not in _REDIRECTS:
            self._fail("no_pending_redirect")
        self._last = None

    def _chunk(self, size):
        if type(size) is not int or size < 0:
            self._fail("invalid_chunk_size")

    def add_request_bytes(self, size: int):
        self._open()
        self._chunk(size)
        if self._active is None or self._status is not None:
            self._fail("request_body_out_of_order")
        if (size > self._active.max_request_bytes - self._request_bytes
                or size > self._policy.limits.max_total_request_bytes - self._total_request_bytes):
            self._fail("request_byte_overflow")
        self._request_bytes += size
        self._total_request_bytes += size

    def start_response(self, status_code: int):
        self._open()
        if self._active is None or self._status is not None:
            self._fail("response_out_of_order")
        if type(status_code) is not int or not 200 <= status_code <= 599:
            self._fail("invalid_final_http_status")
        # Other 3xx responses are not followable redirects in this prototype.
        self._status = status_code

    def add_response_bytes(self, size: int):
        self._open()
        self._chunk(size)
        if self._active is None or self._status is None:
            self._fail("response_body_out_of_order")
        if self._active.method == "HEAD" and size:
            self._fail("head_response_body")
        if (size > self._policy.limits.max_response_bytes - self._response_bytes
                or size > self._policy.limits.max_total_response_bytes - self._total_response_bytes):
            self._fail("response_byte_overflow")
        self._response_bytes += size
        self._total_response_bytes += size

    def finish_response(self) -> ResponseReceipt:
        self._open()
        if self._active is None or self._status is None:
            self._fail("response_not_started")
        receipt = ResponseReceipt(self._requests, self._status, self._active.method,
                                  self._chain, self._request_bytes, self._response_bytes)
        self._active = self._status = None
        self._request_bytes = self._response_bytes = 0
        self._last = receipt
        return receipt

    def abort(self, reason: str):
        """Call in gateway finally paths on timeout, cancel or transport failure."""
        self._open()
        if reason not in ("timeout", "cancelled", "transport_error", "protocol_error", "overflow"):
            self._fail("invalid_abort_reason")
        self._closed = self._incomplete = True
        self._reason = reason

    def finish(self):
        self._open()
        if self._active is not None or (self._last is not None and self._last.status_code in _REDIRECTS):
            self._fail("unfinished_exchange")
        self._closed = True

    def snapshot(self) -> dict:
        return {"task_id": self._policy.task_id, "execution_id": self._policy.execution_id,
                "policy_fingerprint": self._policy.fingerprint,
                "requests": self._requests, "redirects": self._redirects,
                "total_request_bytes": self._total_request_bytes,
                "total_response_bytes": self._total_response_bytes,
                "active_request_bytes": self._request_bytes,
                "active_response_bytes": self._response_bytes,
                "closed": self._closed,
                "incomplete": self._incomplete or self._active is not None,
                "reason": self._reason}
