import asyncio
import json
import pytest
from langchain_core.messages import AIMessage
from src.guardrails.test_turn_checkpoint import store, fake_model
from src.guardrails.turn_checkpoint import build_checkpoint


def test_host_locates_unique_quote_without_model_counting(store, fake_model, monkeypatch):
    from src.core import task_events
    manager, raw = store
    async def invoke(*args, **kwargs):
        return AIMessage(content=json.dumps({'goal':'navigation', 'decisions':[], 'unfinished':[],
            'task_status':'completed', 'excerpts':[{'source_id':'1','quote':'1.23456789'}]}),
            response_metadata={'finish_reason':'stop'})
    monkeypatch.setattr(task_events, 'counted_ainvoke', invoke)
    assert asyncio.run(build_checkpoint(manager, 's', {'answer':'done','request_id':'r1',
        'turn_outcome':{'status':'completed'}}, manager.get_checkpoint('s')))
    excerpt = json.loads(manager.get_checkpoint('s')['summary'])['exact_excerpts'][0]
    assert raw[excerpt['start_char']:excerpt['end_char']] == excerpt['quote'] == '1.23456789'


def test_quote_locator_rejects_ambiguity_missing_and_other_session(store):
    manager, _ = store
    for quote in ('long input', 'not in original'):
        with pytest.raises(ValueError, match='missing_or_ambiguous'):
            manager.locate_context_quote('s', 1, quote)
    with pytest.raises(PermissionError):
        manager.locate_context_quote('other', 1, '1.23456789')
