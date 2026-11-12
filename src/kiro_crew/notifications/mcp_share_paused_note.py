"""One bell note when a shared MCP server that was approved gets paused.

A gateway start refuses a shared server's launch when it differs from every
approved one (``changed_needs_reapproval``). The server then runs one copy per
chat, so nothing in a chat says so. This publishes one ``system.approval`` note
naming the paused servers, with a link to the page where they are approved.

The note carries a ``key``, so the bell holds one such note. A start that finds
the same servers paused as the stored note names publishes nothing: the owner
may have read or deleted that note, and a restart is not news.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

CHANNEL = "system.approval"
#: Replaces the stored note of the same source, so the bell holds one.
KEY = "mcp-share-paused"
#: The note field that records which servers it was published for.
NOTE_KEY = "_mcp_share_paused"
#: The MCP Servers page's Sharing view, with its Review dialog opened.
REVIEW_URL = "/mcp?view=sharing&review=1"


def paused_approved_shares() -> list[str]:
    """Share-list names whose approved launch changed, sorted. BLOCKING: reads files."""
    from kiro_crew.config.loader import KiroCrewConfig
    from kiro_crew.mcp_gateway.launch_approval import (
        REFUSED_CHANGED,
        refused_servers,
        refused_shares,
    )

    cfg = KiroCrewConfig.load().mcp_gateway
    if not cfg.enabled:
        return []
    reasons = refused_shares(cfg.stub_servers, refused_servers())
    return sorted(name for name, reason in reasons.items() if reason == REFUSED_CHANGED)


async def publish(state: Any) -> bool:
    """Publish the note for the servers paused now. True when a note was pushed."""
    from kiro_crew.notifications.bus import NotificationPayload

    names = await asyncio.to_thread(paused_approved_shares)
    if not names:
        return False
    for note in getattr(state, "_notification_log", ()):
        record = note.get(NOTE_KEY)
        if isinstance(record, dict) and record.get("names") == names:
            return False
    listed = ", ".join(names)
    note = state.notification_bus.push(
        NotificationPayload(
            source="system",
            channel=CHANNEL,
            # Not the channel's critical default: a paused share blocks no agent
            # turn, each chat starts its own copy. A user override can raise it.
            priority="default",
            kind="agent",
            title="A shared MCP server is paused",
            body=f"Paused: {listed}. Review and approve the update to share again.",
            url=REVIEW_URL,
            key=KEY,
            i18n={"id": "mcp_share_paused", "params": {"names": listed}},
        )
    )
    note[NOTE_KEY] = {"names": names}
    # The delivery already queued its row; persist the record so a restart
    # does not notify for the same servers again.
    await state._rewrite_notifications_async()
    return True
