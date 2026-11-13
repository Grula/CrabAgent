from __future__ import annotations

import datetime
import json
import logging

logger = logging.getLogger(__name__)


def register_meeting_tools(registry):
    @registry.register(
        name="meeting_process",
        description=(
            "Process meeting notes and extract action items. "
            "Automatically creates persistent tasks for each action item "
            "with assignee, deadline, and priority. "
            "Use when the user shares meeting notes, call notes, or discussion summaries."
        ),
        parameters={
            "type": "object",
            "properties": {
                "notes": {
                    "type": "string",
                    "description": "Meeting notes text (free form, bullet points, or structured notes)",
                },
                "meeting_title": {
                    "type": "string",
                    "description": "Optional meeting title or topic",
                },
            },
            "required": ["notes"],
        },
    )
    async def meeting_process(
        notes: str,
        meeting_title: str = "",
        context=None,
    ) -> str:
        from crabagent.core.meeting.processor import (
            format_meeting_result,
            process_meeting_notes,
        )

        # Resolve user_id
        user_id = 1
        if context:
            user_id = int(
                context.metadata.get("user_id", context.metadata.get("uid", 1))
            )

        result = await process_meeting_notes(
            notes,
            meeting_title=meeting_title,
            user_id=user_id,
            context=context,
        )
        return format_meeting_result(result, meeting_title)
