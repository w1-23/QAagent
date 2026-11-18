"""Fail-closed mitmproxy addon prototype, not an enabled execution capability.

One gateway instance belongs to one host-issued execution policy and one
kernel-isolated client IP. TLS/HTTP parsing and certificate validation belong to
mitmproxy; this addon never implements TLS or trusts client policy headers.
Production admission still requires the external namespace/firewall boundary.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import time
from urllib.parse import urljoin

from .egress_policy import (EgressPolicy, PolicyDenied, StreamBudget,
                            authorize_resolved, normalize_host, normalize_https_url, public_ip)


async def resolve_all(host):
    """Trusted gateway resolution; all AF_UNSPEC stream answers, no client DNS."""
    try:
        literal = str(ipaddress.ip_address(host))
    except ValueError:
        records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
            host, 443, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP, flags=0), timeout=5)
        answers = tuple(dict.fromkeys(row[4][0] for row in records))
        if not 1 <= len(answers) <= 32:
            raise PolicyDenied("bounded_dns_answers_required")
        return tuple(public_ip(value) for value in answers)
    else:
        return (public_ip(literal),)


class ReceiptSink:
    """Exclusive, bounded, private append-only metadata, never response bodies.

    Start events precede forwarding. A start without a terminal event means
    incomplete/unknown, not success. Files must remain in gateway control storage;
    the user sandbox receives only the separate public CA certificate.
    """
    def __init__(self, path: Path, *, max_bytes=4 * 1024**2):
        self.path = Path(path)
        if not self.path.is_absolute() or type(max_bytes) is not int or not 16384 <= max_bytes <= 8 * 1024**2:
            raise ValueError("invalid_gateway_ledger")
        for part in (self.path, *self.path.parents):
            if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                raise PermissionError("gateway_ledger_link_denied")
        self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                          | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
        self.max_bytes, self.written, self.failed = max_bytes, 0, False

    def __call__(self, event):
        if self.failed or self.fd is None:
            raise PolicyDenied("gateway_ledger_unavailable")
        data = (json.dumps(event, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode()
        if len(data) > 16384 or len(data) > self.max_bytes - self.written:
            raise PolicyDenied("gateway_ledger_limit")
        try:
            view = memoryview(data)
            while view:
                n = os.write(self.fd, view)
                if not n:
                    raise OSError("gateway_ledger_write_failed")
                view = view[n:]
            os.fsync(self.fd)
        except OSError:
            # A partial/uncertain write cannot be retried as a new clean event.
            self.failed = True
            raise
        self.written += len(data)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def validate_options(options):
    """Refuse runtime/config drift that permits opaque traffic or disables TLS."""
    required = {"connection_strategy": "lazy", "ssl_insecure": False, "http2": False,
                "websocket": False, "rawtcp": False, "upstream_cert": False}
    for key, expected in required.items():
        actual = getattr(options, key, None)
        if type(actual) is not type(expected) or actual != expected:
            raise PolicyDenied("unsafe_gateway_option_" + key)
    for key in ("ignore_hosts", "tcp_hosts", "udp_hosts", "allow_hosts"):
        if getattr(options, key, ()):
            raise PolicyDenied("unsafe_gateway_option_" + key)
    if getattr(options, "http3", False):
        raise PolicyDenied("gateway_http3_unsupported")
    if list(getattr(options, "mode", ())) != ["regular"]:
        raise PolicyDenied("gateway_regular_mode_required")
    # Chunked POSTs are buffered before request(), so enforce a parser-level
    # ceiling as well as each authorization's smaller actual-byte budget.
    if (getattr(options, "body_size_limit", None) != "1048576"
            or getattr(options, "stream_large_bodies", None) is not None):
        raise PolicyDenied("gateway_bounded_request_buffer_required")


class GatewayGuard:
    """One sequential HTTP exchange at a time; clients retry concurrency errors.

    Helpers operate on mitmproxy flow interfaces, with injection for offline
    contract tests. No hook exception is relied on to block traffic: denial
    explicitly kills the flow/connection before returning to the proxy core.
    """
    def __init__(self, policy: EgressPolicy, *, client_ip: str, sink,
                 response_factory, resolver=resolve_all, deadline_sec=30):
        if type(policy) is not EgressPolicy or policy.mode != "research_https":
            raise PolicyDenied("gateway_research_policy_required")
        self.client_ip = str(ipaddress.ip_address(client_ip))
        if type(deadline_sec) is not int or not 1 <= deadline_sec <= 120:
            raise PolicyDenied("invalid_gateway_deadline")
        self.deadline_sec = deadline_sec
        self.clients = set()
        self.policy, self.sink = policy, sink
        self.response_factory, self.resolver = response_factory, resolver
        self.budget = StreamBudget(policy)
        self.tunnels, self.connections, self.flows, self.resolving = {}, {}, {}, {}
        self.active = None
        self.closed = False
        self.pending = None
        self.attempts = self.redirect_responses = 0

    def _event(self, event, flow, **extra):
        self.sink({"event": event, "task_id": self.policy.task_id,
            "execution_id": self.policy.execution_id, "policy_fingerprint": self.policy.fingerprint,
            "flow_id": str(flow.id), "time": time.time(), **extra})

    @staticmethod
    def _kill(flow):
        # kill() itself raises on an already killed/non-live mitmproxy flow.
        # Also fence the connections so a delayed connection hook cannot resume.
        if not flow.client_conn.error:
            flow.client_conn.error = "egress_flow_denied"
        if not flow.server_conn.error:
            flow.server_conn.error = "egress_flow_denied"
        if flow.killable:
            flow.kill()

    def _abort(self, flow, reason):
        try:
            state = self.flows.pop(flow.id, None)
            self.resolving.pop(flow.id, None)
            owned = self.active == flow.id
            if owned:
                self.active = None
            if state is not None:
                state["closed"] = True
                if state.get("timer") is not None:
                    state["timer"].cancel()
                self.connections.pop(flow.server_conn.id, None)
            if owned or state is not None:
                try:
                    self.budget.abort(reason)
                except PolicyDenied:
                    pass
                self._event("response_incomplete", flow, reason=reason,
                    observed_sha256=state["hash"].hexdigest() if state else None,
                    budget=self.budget.snapshot())
        except Exception:
            pass  # A broken/full audit sink never permits traffic.
        finally:
            self._kill(flow)

    def _deny(self, flow, code):
        # Bounded fixed error text, never raw exception text. Kill even if the
        # response factory or audit sink fails inside the denial path itself.
        try:
            if flow.id in self.flows:
                self._abort(flow, "protocol_error")
            else:
                self._event("request_denied", flow, reason=code)
                flow.response = self.response_factory(403, b'{"error":"egress_policy_denied"}',
                    {"Content-Type": "application/json", "Connection": "close"})
        except Exception:
            pass
        finally:
            self._kill(flow)

    def client_connected(self, client):
        try:
            if self.closed or str(ipaddress.ip_address(client.peername[0])) != self.client_ip or len(self.clients) >= 8:
                client.error = "egress_client_denied"
            else:
                self.clients.add(client.id)
        except (TypeError, ValueError, IndexError):
            client.error = "egress_client_denied"

    def client_disconnected(self, client):
        self.tunnels.pop(client.id, None)
        self.clients.discard(client.id)
        for flow in tuple(self.resolving.values()):
            if flow.client_conn.id == client.id:
                self._abort(flow, "transport_error")
        for state in tuple(self.flows.values()):
            if state["flow"].client_conn.id == client.id and not state["closed"]:
                self._abort(state["flow"], "transport_error")

    def http_connect(self, flow):
        """Allow only intercepted outer HTTPS CONNECT, never opaque tunnelling."""
        try:
            if (self.closed or flow.client_conn.id not in self.clients
                    or flow.client_conn.error or not flow.killable
                    or str(ipaddress.ip_address(flow.client_conn.peername[0])) != self.client_ip):
                raise PolicyDenied("foreign_client")
            self.attempts += 1
            if self.attempts > self.policy.limits.max_requests * 2:
                raise PolicyDenied("connection_attempt_limit")
            destination = normalize_https_url("https://" + flow.request.authority + "/")
            if (flow.request.method != "CONNECT" or flow.request.port != 443
                    or normalize_host(flow.request.host) != destination.host
                    or (self.policy.allowed_hosts and destination.host not in self.policy.allowed_hosts)):
                raise PolicyDenied("invalid_connect")
            self.tunnels[flow.client_conn.id] = destination.host
        except Exception:
            self._deny(flow, "invalid_connect")

    @staticmethod
    def _header_limit(headers):
        fields = headers.fields
        if len(fields) > 100 or sum(len(k) + len(v) + 4 for k, v in fields) > 32768:
            raise PolicyDenied("gateway_header_limit")

    async def requestheaders(self, flow):
        try:
            request, client = flow.request, flow.client_conn
            self._header_limit(request.headers)
            destination = normalize_https_url(request.url)
            tunnel = self.tunnels.get(client.id)
            if (self.closed or client.id not in self.clients or client.error or not flow.killable
                    or str(ipaddress.ip_address(client.peername[0])) != self.client_ip
                    or not client.tls_established or request.scheme != "https"
                    or tunnel != destination.host or request.port != 443):
                raise PolicyDenied("tls_tunnel_binding_required")
            sni = client.sni
            if sni is not None and normalize_host(sni) != destination.host:
                raise PolicyDenied("sni_mismatch")
            if sni is None:
                ipaddress.ip_address(destination.host)  # DNS targets require SNI.
            hosts = request.headers.get_all("host")
            if len(hosts) != 1 or normalize_https_url("https://" + hosts[0] + "/").host != destination.host:
                raise PolicyDenied("host_mismatch")
            if request.headers.get("upgrade") or request.method == "CONNECT":
                raise PolicyDenied("nested_tunnel_denied")
            if self.active is not None:
                raise PolicyDenied("gateway_exchange_busy")
            # Reserve before awaiting DNS; other hooks cannot race the budget.
            self.active = flow.id
            self.resolving[flow.id] = flow
            deadline = time.monotonic() + self.deadline_sec
            addresses = await asyncio.wait_for(self.resolver(destination.host), timeout=min(5, self.deadline_sec))
            # Disconnect/cancel/shutdown may happen while the resolver awaits.
            if (self.closed or self.active != flow.id or not flow.killable
                    or client.id not in self.clients or client.error
                    or self.tunnels.get(client.id) != destination.host):
                raise PolicyDenied("request_revoked_during_resolution")
            self.resolving.pop(flow.id, None)
            authorization = authorize_resolved(self.policy, task_id=self.policy.task_id,
                execution_id=self.policy.execution_id, url=destination.url, method=request.method,
                resolved_ips=addresses, selected_peer=addresses[0])
            redirect = None
            if self.pending is not None:
                url, previous = self.pending
                if url == destination.url:
                    redirect = previous
                else:
                    self.budget.discard_redirect()
                self.pending = None
            self.budget.start_request(authorization, redirect_from=redirect)
            state = {"flow": flow, "authorization": authorization, "hash": hashlib.sha256(),
                     "closed": False, "started": False}
            self.flows[flow.id] = state
            state["timer"] = asyncio.get_running_loop().call_later(
                max(0, deadline - time.monotonic()), self._abort, flow, "timeout")
            self.connections[flow.server_conn.id] = state
            request.stream = False  # Buffer capped POST before any forwarding.
            content_length = request.headers.get("content-length")
            if content_length is not None and (not content_length.isascii() or not content_length.isdecimal()
                    or int(content_length) > authorization.max_request_bytes):
                raise PolicyDenied("request_body_limit")
            if request.headers.get("content-encoding", "identity").lower() != "identity":
                raise PolicyDenied("compressed_request_unsupported")
            for header in ("proxy-authorization", "authorization", "cookie", "x-api-key", "api-key",
                           "forwarded", "x-forwarded-for"):
                request.headers.pop(header, None)
            request.headers["Accept-Encoding"] = "identity"
            request.headers["Connection"] = "close"
            self._event("request_authorized", flow, authorization=authorization.as_dict())
        except asyncio.CancelledError:
            self._abort(flow, "cancelled")
            raise
        except Exception:
            if self.active == flow.id and flow.id not in self.flows:
                self.active = None
            self._deny(flow, "request_denied")
        finally:
            self.resolving.pop(flow.id, None)

    def request(self, flow):
        try:
            state = self.flows[flow.id]
            content = flow.request.raw_content
            if state["closed"] or type(content) is not bytes:
                raise PolicyDenied("buffered_request_required")
            self.budget.add_request_bytes(len(content))
            state["started"] = True
        except Exception:
            self._deny(flow, "request_body_denied")

    async def server_connect(self, data):
        try:
            state = self.connections[data.server.id]
            flow, binding = state["flow"], state["authorization"]
            if (state["closed"] or self.active != flow.id or not state["started"]
                    or data.client.id != flow.client_conn.id):
                raise PolicyDenied("unbound_server_connection")
            # A numeric address prevents an implicit second DNS lookup. mitmproxy
            # retains the hostname SNI for strict upstream certificate validation.
            data.server.address = (binding.selected_peer, 443)
            data.server.sni = binding.destination.host
        except Exception:
            data.server.error = "egress_server_denied"

    def server_connected(self, data):
        try:
            state = self.connections[data.server.id]
            binding = state["authorization"]
            peer, port = data.server.peername[:2]
            if public_ip(peer) != binding.selected_peer or port != 443:
                raise PolicyDenied("actual_peer_mismatch")
        except Exception:
            data.server.error = "egress_actual_peer_denied"
            if data.server.id in self.connections:
                self._abort(self.connections[data.server.id]["flow"], "transport_error")

    def server_connect_error(self, data):
        state = self.connections.get(data.server.id)
        if state is not None:
            self._abort(state["flow"], "transport_error")

    def responseheaders(self, flow):
        try:
            state = self.flows[flow.id]
            response = flow.response
            self._header_limit(response.headers)
            if state["closed"] or response.headers.get("upgrade") or response.status_code == 101:
                raise PolicyDenied("response_upgrade_denied")
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise PolicyDenied("compressed_response_unsupported")
            if response.status_code in (301, 302, 303, 307, 308):
                self.redirect_responses += 1
                if self.redirect_responses > self.policy.limits.max_total_redirects:
                    raise PolicyDenied("redirect_response_limit")
                location = response.headers.get("location")
                if not location:
                    raise PolicyDenied("redirect_location_required")
                destination = normalize_https_url(urljoin(state["authorization"].destination.url, location))
                state["redirect_url"] = destination.url
            self.budget.start_response(response.status_code)
            response.headers["Connection"] = "close"
            # Transfer decoding belongs to mitmproxy. Compressed representation
            # is rejected so the observed byte hash has an unambiguous identity.
            def stream(chunk):
                try:
                    if state["closed"] or type(chunk) is not bytes:
                        raise PolicyDenied("response_stream_closed")
                    self.budget.add_response_bytes(len(chunk))
                    state["hash"].update(chunk)
                    return chunk
                except Exception:
                    self._abort(flow, "overflow")
                    return b""  # Do not forward the over-limit chunk.
            response.stream = stream
        except Exception:
            self._deny(flow, "response_denied")

    def response(self, flow):
        state = self.flows.get(flow.id)
        if state is None or state["closed"]:
            return
        try:
            receipt = self.budget.finish_response()
            self._event("response_complete", flow, response=asdict(receipt),
                observed_sha256=state["hash"].hexdigest(), selected_peer=state["authorization"].selected_peer)
            if "redirect_url" in state:
                self.pending = (state["redirect_url"], receipt)
            state["closed"] = True
            state["timer"].cancel()
            self.active = None
            self.connections.pop(flow.server_conn.id, None)
            self.flows.pop(flow.id, None)
        except Exception:
            self._abort(flow, "transport_error")

    def error(self, flow):
        if flow.id in self.flows:
            self._abort(flow, "transport_error")


class MitmproxyGateway:
    """Adapter loaded by mitmdump; optional dependency never imported by Web."""
    guard = None
    sink = None

    def load(self, loader):
        loader.add_option("citrus_policy_file", str, "", "Host-owned bounded execution policy")
        loader.add_option("citrus_client_ip", str, "", "Single kernel-isolated client IPv4")
        loader.add_option("citrus_receipt_file", str, "", "Exclusive private response metadata ledger")

    def configure(self, updated):
        from mitmproxy import ctx, http
        try:
            validate_options(ctx.options)
            if self.guard is not None:
                raise PolicyDenied("gateway_live_reconfiguration_denied")
        except Exception:
            # Hook exceptions alone are not enforcement. Revoke live flows even
            # if the proxy core catches/reports an invalid option update.
            self.done()
            raise
        path = Path(ctx.options.citrus_policy_file)
        if not path.is_absolute() or path.is_symlink() or path.stat().st_size > 32768:
            raise PolicyDenied("gateway_policy_file_required")
        policy = EgressPolicy.parse(path.read_text(encoding="utf-8"))
        self.sink = ReceiptSink(Path(ctx.options.citrus_receipt_file))
        try:
            self.guard = GatewayGuard(policy, client_ip=ctx.options.citrus_client_ip,
                                      sink=self.sink, response_factory=http.Response.make)
        except Exception:
            self.done()
            raise

    def _dispatch(self, name, subject):
        if self.guard is not None:
            return getattr(self.guard, name)(subject)
        if name.startswith("client_"):
            subject.error = "gateway_not_configured"
        elif name.startswith("server_"):
            subject.server.error = "gateway_not_configured"
        else:
            GatewayGuard._kill(subject)

    # Real methods are discoverable before configure(). Async hooks must remain
    # async at the adapter boundary; returning an unawaited coroutine is unsafe.
    def client_connected(self, client):
        self._dispatch("client_connected", client)

    def client_disconnected(self, client):
        self._dispatch("client_disconnected", client)

    def http_connect(self, flow):
        self._dispatch("http_connect", flow)

    async def requestheaders(self, flow):
        result = self._dispatch("requestheaders", flow)
        if result is not None:
            await result

    def request(self, flow):
        self._dispatch("request", flow)

    async def server_connect(self, data):
        result = self._dispatch("server_connect", data)
        if result is not None:
            await result

    def server_connected(self, data):
        self._dispatch("server_connected", data)

    def server_connect_error(self, data):
        self._dispatch("server_connect_error", data)

    def responseheaders(self, flow):
        self._dispatch("responseheaders", flow)

    def response(self, flow):
        self._dispatch("response", flow)

    def error(self, flow):
        self._dispatch("error", flow)

    def done(self):
        if self.guard is not None:
            self.guard.closed = True
            for flow in tuple(self.guard.resolving.values()):
                self.guard._abort(flow, "cancelled")
            for state in tuple(self.guard.flows.values()):
                self.guard._abort(state["flow"], "cancelled")
        if self.sink is not None:
            self.sink.close()
