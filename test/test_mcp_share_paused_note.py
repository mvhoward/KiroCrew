"""One bell note when a shared MCP server that was approved gets paused.

Contract under test:
- a share-list server refused as ``changed_needs_reapproval`` is named in one
  ``system.approval`` note that links to the Sharing view's Review dialog;
- a server never approved (``added_outside_dashboard``) and a refused server
  that is not on the share list raise no note, and sharing off raises none;
- a later start that finds the same servers paused publishes nothing, read or
  not, and a different set publishes again under the same key.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kiro_crew.mcp_gateway import launch_approval
from kiro_crew.notifications import mcp_share_paused_note as note_mod
from kiro_crew.notifications.bus import NotificationBus

CHANGED = launch_approval.REFUSED_CHANGED
UNAPPROVED = launch_approval.REFUSED_UNAPPROVED

pytestmark = pytest.mark.asyncio


class _State:
    """What the note publisher reads off the dashboard state."""

    def __init__(self) -> None:
        self._notification_log: list[dict] = []
        self.notification_bus = NotificationBus(sink=self._notification_log.append)
        self.rewrites = 0

    async def _rewrite_notifications_async(self) -> None:
        self.rewrites += 1


def _world(
    monkeypatch: pytest.MonkeyPatch,
    *,
    refused: dict[str, str],
    shared: tuple[str, ...] = ("alpha-mcp", "beta-mcp"),
    enabled: bool = True,
) -> None:
    """Sharing config and the approval store's refusals, as one gateway start left them."""
    from kiro_crew.config import loader

    gateway = SimpleNamespace(enabled=enabled, stub_servers=list(shared))
    monkeypatch.setattr(
        loader.KiroCrewConfig, "load", classmethod(lambda cls: SimpleNamespace(mcp_gateway=gateway))
    )
    monkeypatch.setattr(
        launch_approval,
        "refused_servers",
        lambda path=None: {name: {"reason": reason} for name, reason in refused.items()},
    )


async def test_names_each_approved_share_that_is_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    _world(monkeypatch, refused={"beta-mcp": CHANGED, "alpha-mcp": CHANGED})
    state = _State()

    assert await note_mod.publish(state) is True

    (note,) = state._notification_log
    assert note["channel"] == "system.approval"
    assert note["priority"] == "default"
    assert note["url"] == "/mcp?view=sharing&review=1"
    assert note["key"] == note_mod.KEY
    assert note["body"] == (
        "Paused: alpha-mcp, beta-mcp. Review and approve the update to share again."
    )
    assert note["i18n"] == {"id": "mcp_share_paused", "params": {"names": "alpha-mcp, beta-mcp"}}
    assert state.rewrites == 1


@pytest.mark.parametrize(
    ("refused", "shared", "enabled"),
    [
        pytest.param({"alpha-mcp": UNAPPROVED}, ("alpha-mcp",), True, id="never-approved"),
        pytest.param({"other-mcp": CHANGED}, ("alpha-mcp",), True, id="not-on-the-share-list"),
        pytest.param({"alpha-mcp": CHANGED}, ("alpha-mcp",), False, id="sharing-off"),
        pytest.param({}, ("alpha-mcp",), True, id="nothing-refused"),
    ],
)
async def test_raises_no_note(
    monkeypatch: pytest.MonkeyPatch,
    refused: dict[str, str],
    shared: tuple[str, ...],
    enabled: bool,
) -> None:
    _world(monkeypatch, refused=refused, shared=shared, enabled=enabled)
    state = _State()

    assert await note_mod.publish(state) is False

    assert state._notification_log == []
    assert state.rewrites == 0


async def test_the_same_paused_servers_are_not_announced_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _world(monkeypatch, refused={"alpha-mcp": CHANGED})
    state = _State()
    assert await note_mod.publish(state) is True
    # Read by the owner: a restart with the same servers paused is still not news.
    state._notification_log[0]["acked"] = True

    assert await note_mod.publish(state) is False

    assert len(state._notification_log) == 1


async def test_a_different_set_is_announced_under_the_same_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _world(monkeypatch, refused={"alpha-mcp": CHANGED})
    state = _State()
    assert await note_mod.publish(state) is True
    _world(monkeypatch, refused={"alpha-mcp": CHANGED, "beta-mcp": CHANGED})

    assert await note_mod.publish(state) is True

    newest = state._notification_log[-1]
    assert newest["key"] == note_mod.KEY
    assert newest["i18n"]["params"] == {"names": "alpha-mcp, beta-mcp"}
