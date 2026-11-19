"""A plain figure-detail read must not take a figure write slot."""
import asyncio
import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from src.api import figure_router
from src.core import figure_capacity
from src.figures import projects


class _Lease:
    """The minimum a capacity reservation needs from an account lease."""

    def __init__(self, owner: int):
        self.owner = int(owner)
        self.finalizers = []

    def add_finalizer(self, callback) -> None:
        self.finalizers.append(callback)

    def retain(self):
        return self

    def release(self) -> None:
        while self.finalizers:
            self.finalizers.pop()()


class FigureDetailCapacityTests(unittest.TestCase):
    def setUp(self):
        self.owner = 4242
        self.request = Request({
            "type": "http", "method": "GET", "path": "/", "headers": [],
            "query_string": b"", "scheme": "http",
            "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
        })
        self.request.state.user = {"user_id": self.owner}
        self.active = {}
        self.enterContext(patch.object(figure_capacity, "_active", self.active))
        self.lease_entries = []

        @contextmanager
        def fake_account_lease(owner):
            self.lease_entries.append(int(owner))
            yield _Lease(owner)

        self.enterContext(patch.object(figure_router, "account_lease", fake_account_lease))

        async def fake_run_account_thread(lease, func, /, *args, **kwargs):
            return func(*args, **kwargs)

        self.enterContext(patch.object(figure_router, "run_account_thread",
                                       fake_run_account_thread))
        self.detail_calls = []

        def fake_detail(figure_id, owner, *, upgrade_manifest=False):
            self.detail_calls.append((figure_id, owner, upgrade_manifest))
            return {"figure_id": figure_id, "render_version": 1}

        self.enterContext(patch.object(projects, "detail_for_owner", fake_detail))

    def test_plain_detail_read_needs_no_write_slot(self):
        total = figure_capacity._positive("CITRUS_FIGURE_ACTIVE_TOTAL", 4)
        # An unrelated account already holds every render slot.
        self.active[self.owner + 1] = total

        response = asyncio.run(figure_router.get_project("figure-1", self.request))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)["figure_id"], "figure-1")
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        # The read was neither refused with 429 nor charged a write slot.
        self.assertEqual(self.active, {self.owner + 1: total})
        # Authentication/ownership and the account-deletion lease still ran.
        self.assertEqual(self.lease_entries, [self.owner])
        self.assertEqual(self.detail_calls, [("figure-1", self.owner, False)])

    def test_prepare_edit_keeps_the_write_reservation(self):
        total = figure_capacity._positive("CITRUS_FIGURE_ACTIVE_TOTAL", 4)
        self.active[self.owner + 1] = total

        with self.assertRaises(HTTPException) as caught:
            asyncio.run(figure_router.get_project("figure-1", self.request, prepare_edit=True))

        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.detail["code"], "figure_capacity")
        self.assertEqual(self.detail_calls, [])
        self.assertEqual(self.active, {self.owner + 1: total})
