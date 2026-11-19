"""Production operation/publishing adapters for the generic capability scheduler.

These adapters reuse account permissions, durable operation leases, artifact
verification and full-result storage. They do not own a model loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
import uuid

from langchain_core.messages import ToolMessage

from .execution import ToolExecution
from .identity import ExecutionIdentity


@dataclass
class HostTools:
    identity: ExecutionIdentity
    state: dict
    records: list[dict] = field(default_factory=list)
    messages: dict[str, ToolMessage] = field(default_factory=dict)
    dispatched: dict[str, str] = field(default_factory=dict)
    main_results: list = field(default_factory=list)
    web_results: list = field(default_factory=list)
    pending_question: dict | None = None
    allowed_names: set[str] = field(default_factory=set)
    published_resolutions: dict[str, str] = field(default_factory=dict)

    def publish_resolutions(self, outcome=None):
        from src.core.task_result import resolved_tool_attempts
        from src.core.progress_bus import emit_encoded
        resolutions = {item['tool_call_id']: item for item in resolved_tool_attempts(self.records, outcome)}
        # Pure admission rejections can also resolve before a final answer, e.g.
        # a corrected request_user_input that intentionally pauses the loop.
        for index, row in enumerate(self.records):
            cid = row.get('tool_call_id')
            if (row.get('status') != 'not_run' or not cid
                    or (row.get('reason') or row.get('reason_code')) not in {
                        'invalid_tool_arguments', 'required_skill_not_loaded', 'script_execution_rejected',
                        'workspace_version_conflict'}
                    or not self.proven_no_dispatch(cid)):
                continue
            target = next((later.get('tool_call_id') for later in self.records[index + 1:]
                if later.get('name') == row.get('name') and later.get('status') == 'complete'), None)
            if target:
                resolutions[cid] = {'tool_call_id': cid, 'resolved_by_tool_call_id': target, 'resolution': 'repaired'}
        changes = []
        for item in resolutions.values():
            cid, target = item["tool_call_id"], item["resolved_by_tool_call_id"]
            if self.published_resolutions.get(cid) != target:
                changes.append(item)
                self.published_resolutions[cid] = target
        if changes:
            emit_encoded("tool_resolution", {"resolutions": changes})

    def _result_request_id(self):
        """Result storage uses the job's client turn key, not its server trace ID."""
        from src.core import jobs
        from src.core.task_events import TaskConflict
        job = jobs.get_job(self.identity.run_id)
        if (not job or job['owner_user_id'] != self.identity.owner_user_id
                or job['session_id'] != self.identity.session_id
                or job['request_id'] != self.identity.user_turn_id):
            raise TaskConflict('agent_execution_identity_conflict')
        return str(job.get('client_request_id') or self.identity.user_turn_id)

    def proven_no_dispatch(self, cid):
        observation = self.messages.get(cid)
        if observation is None or observation.additional_kwargs.get('operation_uncertain'):
            return False
        if cid not in self.dispatched and not observation.additional_kwargs.get('execution_dispatched'):
            return True
        marker = observation.additional_kwargs.get('host_preflight_reconciled')
        if not isinstance(marker, dict):
            return False
        import hashlib
        from src.core import tool_results
        stored = tool_results.fetch(cid)
        return bool(marker.get('schema_version') == 1 and marker.get('operation_id')
            and marker['operation_id'] == observation.additional_kwargs.get('operation_id')
            and stored and stored.get('session_id') == self.identity.session_id
            and stored.get('request_id') == self._result_request_id()
            and tool_results.storage_integrity(stored)['complete']
            and marker.get('result_sha256') == hashlib.sha256(stored['result_text'].encode('utf-8')).hexdigest())

    def assert_context(self):
        from src.core import jobs, tracing
        from src.core.task_events import TaskConflict
        job = jobs.get_job(self.identity.run_id)
        if (tracing.get_job_id() != self.identity.run_id
                or tracing.get_user_id() != self.identity.owner_user_id
                or tracing.get_session_id() != self.identity.session_id
                or not job or job["owner_user_id"] != self.identity.owner_user_id
                or job["session_id"] != self.identity.session_id
                or job["request_id"] != self.identity.user_turn_id
                or job["status"] not in {"running", "detached"}):
            raise TaskConflict("agent_execution_identity_conflict")

    @staticmethod
    def _retry_rejected(call):
        reason = 'browse_retry_rejected'
        return ToolExecution(ToolMessage(content=json.dumps({'error': reason}), status='error',
            name=call['name'], tool_call_id=call['id'],
            artifact={'_tool_outcome': {'status': 'not_run', 'reason_code': reason}}))

    def _browse_receipt(self, message):
        from src.core.task_events import TaskConflict, operation_result
        operation_id = message.additional_kwargs.get('operation_id')
        if not operation_id or message.additional_kwargs.get('operation_uncertain'):
            return None
        try:
            receipt = operation_result(self.identity.run_id, operation_id, include_retry=True)
        except TaskConflict:
            return None
        reference = message.additional_kwargs.get('result_reference') or {}
        digest = hashlib.sha256(receipt['result']['content'].encode('utf-8')).hexdigest()
        return receipt if reference.get('full_sha256') == digest else None

    def _retry_target(self, message, record):
        from src.tools.browse_web import NAVIGATION_FAILURES
        if (message.name != 'browse_web' or record.get('status') != 'failed'
                or record.get('reason') not in NAVIGATION_FAILURES):
            return None
        self.validate_observation(message)
        receipt = self._browse_receipt(message)
        if (not receipt or receipt['outcome'].get('status') != 'failed'
                or receipt['outcome'].get('reason_code') != record['reason']):
            return None
        target = receipt['result']['artifact'].get('_browse_retry_target')
        return {**target, 'operation_id': receipt['operation_id']} if target else None

    def _find_retry_parent(self, operation_id):
        for record in self.records:
            message = self.messages.get(record.get('tool_call_id'))
            if message and message.additional_kwargs.get('operation_id') == operation_id:
                return message, record
        return None, None

    def _claim_target(self, call, retry_of):
        if not isinstance(retry_of, str) or not re.fullmatch(r'[a-f0-9]{32}', retry_of):
            return None
        parent, row = self._find_retry_parent(retry_of)
        if parent is None:
            return None
        try:
            target = self._retry_target(parent, row)
        except RuntimeError:
            return None
        args = call['args']
        if (not target or args.get('action') != target['action']
                or (args.get('tab_id') and args['tab_id'] != target['tab_id'])):
            return None
        from src.browse.relay import BrowseError, Relay
        from urllib.parse import urlsplit
        try:
            host = urlsplit(target['url']).hostname
            destination = Relay(frozenset({host})).authorize(args.get('url'))
        except BrowseError:
            return None
        return target if destination.url == target['url'] else None

    def _link_successful_retry(self, message, record):
        record.pop('retry_resolves_call_id', None)
        if message.name != 'browse_web' or record.get('status') != 'complete':
            return
        receipt = self._browse_receipt(message)
        if not receipt or not receipt['retry_of'] or receipt['outcome'].get('status') != 'complete':
            return
        parent, original = self._find_retry_parent(receipt['retry_of'])
        if parent and self._retry_target(parent, original):
            record['retry_resolves_call_id'] = parent.tool_call_id

    async def invoke(self, tool, call, identity):
        if identity != self.identity:
            raise ValueError("operation_identity_mismatch")
        self.assert_context()
        from src.tools.registry import _run_single_tool
        from src.core.execution_ledger import current_execution_ledger
        from src.core.progress_bus import emit_tool_call_start, emit_tool_executing
        from src.tools.interaction import bind_question_context, reset_question_context
        from src.core.task_events import TaskConflict
        logical_key = call['_host_logical_key'] = 'host:' + uuid.uuid4().hex
        retry_of = call['args'].get('retry_of') if call['name'] == 'browse_web' else None
        retry_target = self._claim_target(call, retry_of) if retry_of else None
        if retry_of and retry_target is None:
            return self._retry_rejected(call)
        attempt = 1
        if retry_target:
            _, parent = self._find_retry_parent(retry_of)
            attempt += parent['attempt']
        call['_attempt'] = attempt
        ledger = current_execution_ledger()
        if ledger:
            ledger.start(call["id"], call["name"], call["args"], source="provider",
                         logical_key=logical_key, attempt=attempt)
        emit_tool_call_start(call["name"], call["args"], call["id"],
                             logical_key=logical_key, attempt=attempt)
        emit_tool_executing("正在执行所选操作", call["name"], call["id"],
                            logical_key=logical_key, attempt=attempt)
        token = bind_question_context(self.question_context())
        from src.core import tracing
        from src.graph.retrieval_router import retrieval_denied, source_scope
        query = str(self.state.get("query") or "")
        previous_network = tracing.web_search_enabled()
        # Composite tools (e.g. a durable reference audit) also make HTTP calls.
        # Propagate explicit source restrictions to their existing network gate;
        # catalogue metadata alone cannot enforce a composite tool's internals.
        tracing.set_web_search_enabled(previous_network
            and source_scope(query, exclusive_only=True) != "local"
            and not retrieval_denied(query, "web"))
        def dispatched():
            self.dispatched[call["id"]] = call["name"]
        try:
            from contextlib import nullcontext
            from .code_tools import CODE_TOOL_NAMES, EXECUTION_WAIT_TOOL_NAMES, code_execution_context
            binding = (code_execution_context(identity, call['id'])
                       if call['name'] in CODE_TOOL_NAMES else nullcontext())
            retry_binding = nullcontext()
            if retry_target:
                from src.tools.browse_web import bind_navigation_retry
                retry_binding = bind_navigation_retry(retry_target)
            with binding, retry_binding:
                message = await _run_single_tool(tool, call, require_durable=True,
                                                 on_dispatch=dispatched, retry_of=retry_of or None)
        except TaskConflict as exc:
            if str(exc) != 'browse_retry_rejected':
                raise
            return self._retry_rejected(call)
        finally:
            tracing.set_web_search_enabled(previous_network)
            reset_question_context(token)
        # Preserve the real observation even if publishing subsequently fails.
        self.messages[call["id"]] = message
        artifact = message.artifact if isinstance(message.artifact, dict) else {}
        question = artifact.get("user_question")
        if call["name"] == "request_user_input" and isinstance(question, dict):
            self.pending_question = question
        execution_waits = ()
        if (call['name'] in EXECUTION_WAIT_TOOL_NAMES
                and artifact.get('_code_execution_id')
                and (artifact.get('_tool_outcome') or {}).get('status') == 'complete'):
            import asyncio
            from src.code_execution.service import ExecutionService
            from src.code_execution.waits import ExecutionWaits
            # Only the trusted adapter's authenticated execution reference is
            # adopted. The model cannot supply wait IDs, claims or acknowledgments.
            waits = ExecutionWaits(ExecutionService(identity))
            watched = await asyncio.to_thread(waits.watch, artifact['_code_execution_id'],
                                              tool_call_id=call['id'])
            execution_waits = (watched['wait_id'],)
        return ToolExecution(message,
            operation_id=str(message.additional_kwargs.get("operation_id") or ""),
            uncertain=bool(message.additional_kwargs.get("operation_uncertain")),
            pending_question=self.pending_question is not None,
            execution_waits=execution_waits)

    async def publish(self, call, execution, identity):
        if identity != self.identity:
            raise ValueError("publication_identity_mismatch")
        from src.graph.agent_operations import _emit_completed_tool_step, _capture_interaction_refs
        from src.core.tool_result_view import first_delivery
        message = execution.message
        self.messages[call["id"]] = message
        if '_host_logical_key' not in call:
            call['_host_logical_key'] = 'host:' + uuid.uuid4().hex
        _emit_completed_tool_step(call, message, self.records)
        storage = message.additional_kwargs.get("result_storage") or {}
        if not storage.get("complete"):
            # Do not keep working from an observation we cannot read back.
            raise RuntimeError("operation_result_not_persisted")
        from src.core.tool_results import pin_references
        if not pin_references(identity.session_id, [message.tool_call_id]):
            raise RuntimeError("checkpoint_evidence_pin_failed")
        _capture_interaction_refs(self.state, [message])
        artifact = message.artifact if isinstance(message.artifact, dict) else {}
        self.main_results.extend(artifact.get("main_results") or [])
        self.web_results.extend(artifact.get("web_results") or [])
        delivered = first_delivery(message, call=call,
                                   reader_available="read_tool_result" in self.allowed_names)
        record = self.records[-1]
        delivered.additional_kwargs['host_publication'] = record
        if delivered.name == 'browse_web':
            self.validate_observation(delivered)
            self._link_successful_retry(delivered, record)
            if target := self._retry_target(delivered, record):
                payload = json.loads(delivered.content)
                payload['host_browse_retry'] = {'retry_of': target['operation_id'], 'tab_id': target['tab_id']}
                delivered = delivered.model_copy(update={'content': json.dumps(payload, ensure_ascii=False)})
        delivered.additional_kwargs['host_publication'] = dict(record)
        self.messages[call["id"]] = delivered
        self.publish_resolutions()
        return delivered

    def validate_observation(self, message):
        """Check retained original bytes before adopting checkpoint evidence."""
        self.assert_context()
        from src.core import tool_results
        stored = tool_results.fetch(message.tool_call_id)
        record = message.additional_kwargs.get("host_publication")
        reference = message.additional_kwargs.get("result_reference") or {}
        if (not stored or stored.get("session_id") != self.identity.session_id
                or stored.get("request_id") != self._result_request_id()
                or not tool_results.storage_integrity(stored)["complete"]
                or stored.get("tool_name") != message.name
                or reference.get("full_sha256") != stored.get("full_sha256")
                or not isinstance(record, dict) or record.get("tool_call_id") != message.tool_call_id):
            raise RuntimeError("recovery_result_evidence_unavailable")
        return record

    def restore_observation(self, message):
        """Hydrate a committed outbox result without re-publishing bounded text."""
        record = dict(self.validate_observation(message))
        from src.graph.agent_operations import _capture_interaction_refs
        self._link_successful_retry(message, record)
        self.messages[message.tool_call_id] = message
        self.records.append(record)
        _capture_interaction_refs(self.state, [message])
        artifact = message.artifact if isinstance(message.artifact, dict) else {}
        self.main_results.extend(artifact.get("main_results") or [])
        self.web_results.extend(artifact.get("web_results") or [])
        if message.name == "request_user_input" and isinstance(artifact.get("user_question"), dict):
            self.pending_question = artifact["user_question"]

    def working_state(self):
        """References survive; historical queries do not become permanent constraints."""
        from src.graph.agent_operations import _task_working_state
        working = _task_working_state(self.state, str(self.state.get("query") or ""), self.records, [])
        # Legacy state already contains whole queries disguised as constraints.
        # Keep real scoped constraints, but don't perpetuate the legacy ':user'
        # entries; their original text remains in searchable session history.
        previous = self.state.get("context_working_state") or {}
        working["retired_constraint_ids"] = [item["source_id"] for item in previous.get("constraints", [])
            if isinstance(item, dict) and str(item.get("source_id") or "").endswith(":user")]
        working["constraints"] = [item for item in previous.get("constraints", [])
            if isinstance(item, dict) and not str(item.get("source_id") or "").endswith(":user")]
        return working

    def question_context(self):
        working = self.working_state()
        return {"query": str(self.state.get("query") or ""),
            "context_working_state": working,
            **{key: working.get(key, []) for key in ("source_refs", "dataset_refs", "script_refs", "run_refs")},
            "attachment_refs": self.state.get("attachment_refs") or [],
            "visualization_enabled": bool(self.state.get("visualization_enabled")),
            "selected_skills": list(self.state.get("selected_skills") or []),
            "context_compression": bool(self.state.get("context_compression", True)),
            "tool_status": [dict(r) for r in self.records], "no_replay": True}
