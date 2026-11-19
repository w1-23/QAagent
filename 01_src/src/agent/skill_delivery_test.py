"""Verified Skill reads expose bounded pages and stable failure codes."""
import asyncio
import json

import pytest

from src.agent import research_skill_catalog as catalog
from src.agent.identity import ExecutionIdentity
from src.agent.skill_adapter import SkillSession
from src.agent.skill_tools import load_skill, read_skill, skill_execution_context


@pytest.mark.parametrize('skill_id', sorted(catalog.PINNED))
def test_release_skill_pages_and_required_resources(skill_id):
    version, digest = catalog.PINNED[skill_id]
    args = {'skill_id': skill_id, 'version': version, 'sha256': digest}
    with skill_execution_context(SkillSession(ExecutionIdentity(1, 'session', 'turn', 'job'), set())):
        first, _ = read_skill.func(max_chars=80, **args)
        first = json.loads(first)['page']
        later, _ = read_skill.func(start_char=first['next_start_char'], max_chars=80, **args)
        full, artifact = load_skill.func(**args)
        wrong, _ = read_skill.func(**{**args, 'sha256': '0' * 64})
        missing, _ = read_skill.func(resource='missing.md', **args)
    assert artifact['_tool_outcome']['status'] == 'complete'
    pages = json.loads(full)['skill_load']
    assert {page['page']['resource'] for page in pages} == set(pages[0]['required_resources'])
    assert json.loads(later)['page']['next_start_char'] == 160
    for rejected in (wrong, missing):
        assert json.loads(rejected)['error'] == 'skill_resource_unavailable'


def test_skill_io_errors_are_redacted_in_separate_request_contexts():
    class UnavailableSkill(SkillSession):
        def page(self, *args, **kwargs):
            assert self.identity.owner_user_id == 2
            raise OSError('private-marker: /var/private/skill/SKILL.md')

    skill = 'candidate-search'
    version, digest = catalog.PINNED[skill]

    async def read(session):
        with skill_execution_context(session):
            await asyncio.sleep(0)
            content, artifact = read_skill.func(skill, version, digest)
            return json.loads(content), artifact['_tool_outcome']

    async def exercise():
        return await asyncio.gather(
            read(SkillSession(ExecutionIdentity(1, 'session-1', 'turn', 'job-1'), set())),
            read(UnavailableSkill(ExecutionIdentity(2, 'session-2', 'turn', 'job-2'), set())),
        )

    (success, _), (failure, outcome) = asyncio.run(exercise())
    assert success['identity']['owner_user_id'] == 1
    assert failure == {'error': 'skill_resource_unavailable', '_tool_outcome': outcome}
    assert outcome['status'] == 'not_run'
