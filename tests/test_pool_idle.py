"""Idle browser contexts are closed; the one in use is kept (no browser)."""

import asyncio
import time

import pytest

from chatsherpa.pool import BrowserPool, _Entry


class _Ctx:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def _entry(age_s, busy=False):
    e = _Entry(context=_Ctx(), page=None)
    e.last_used = time.monotonic() - age_s
    if busy:
        e.lock = asyncio.Lock()
        e.lock.locked = lambda: True  # simulate a send in progress
    return e


async def test_idle_context_is_closed_and_active_one_kept():
    pool = BrowserPool(headless=True, idle_seconds=60)
    idle, fresh = _entry(age_s=600), _entry(age_s=1)
    pool._entries = {("1", "chatgpt"): idle, ("2", "grok"): fresh}
    await pool._drop_idle(keep=None)
    assert ("1", "chatgpt") not in pool._entries and idle.context.closed
    assert ("2", "grok") in pool._entries and not fresh.context.closed


async def test_busy_or_kept_context_is_never_closed():
    pool = BrowserPool(headless=True, idle_seconds=60)
    busy, kept = _entry(age_s=600, busy=True), _entry(age_s=600)
    pool._entries = {("1", "gemini"): busy, ("2", "deepseek"): kept}
    await pool._drop_idle(keep=("2", "deepseek"))
    assert ("1", "gemini") in pool._entries and not busy.context.closed
    assert ("2", "deepseek") in pool._entries and not kept.context.closed


async def test_small_host_defaults():
    pool = BrowserPool(headless=True)
    assert pool.max_contexts == 2 and pool.idle_seconds == 300.0
