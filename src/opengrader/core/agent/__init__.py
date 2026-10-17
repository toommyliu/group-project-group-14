"""Grading through an AI assistant the teacher already uses (Claude Code, Codex, …).

Opengrader does not run the model here. `prepare_agent_packet` hands the
assistant a redacted grader packet (`core/handoff/packet.py`): every student
is an `og-xxxxxxxx` alias, and the scans, the name/ID crops and the handoff
key stay behind. The assistant reads tasks and records its choices through
`opengrader agent …`, which checks each one against the rubric and scores
it with the same arithmetic as the built-in AI grader, tagged
`[AI · NN% · <model> (agent)]` so the teacher confirms or overrides it like
any other AI grade. The packet comes home through grade-merge's three-way
merge, so a teacher's own grade is never overwritten.

Privacy is enforced by what goes into the packet, not by instructions to the
assistant: an assistant can read anything it is pointed at, and whatever it
reads has been sent to its provider. Beyond the packet's own redaction,
`prepare_agent_packet` scrubs the student's name and ID out of answer text
and comments (the merge baseline too, so the scrub never merges back as an
edit), drops scan warnings, and refuses the packet if a name or ID is still
anywhere in it. What cannot be checked is the handwriting inside a crop —
the instructions file says so.
"""

from .common import (
    AGENT_PROVIDER,
    AgentError,
    self_argv,
    find_packet_root,
    guide_text,
    self_command,
)
from .answer_key import KeyWorkspace, check_key_file, prepare_key_workspace, use_key
from .mcp import client_config, client_instructions, serve
from .packet import (
    AgentPacket,
    default_agent_output_dir,
    delete_packet,
    list_agent_packets,
    prepare_agent_packet,
)
from .suggestions import (
    Suggestion,
    accept_suggestion,
    dismiss_suggestion,
    list_suggestions,
    parse_partial,
    suggest,
)
from .tasks import (
    AgentTask,
    PacketStatus,
    ResultOutcome,
    load_tasks,
    packet_status,
    record_grade,
    record_results,
    record_transcription,
)

__all__ = [
    "AGENT_PROVIDER",
    "AgentError",
    "AgentPacket",
    "AgentTask",
    "KeyWorkspace",
    "Suggestion",
    "accept_suggestion",
    "check_key_file",
    "client_config",
    "client_instructions",
    "delete_packet",
    "dismiss_suggestion",
    "list_suggestions",
    "parse_partial",
    "prepare_key_workspace",
    "serve",
    "suggest",
    "use_key",
    "PacketStatus",
    "ResultOutcome",
    "default_agent_output_dir",
    "find_packet_root",
    "guide_text",
    "list_agent_packets",
    "load_tasks",
    "packet_status",
    "prepare_agent_packet",
    "record_grade",
    "record_results",
    "record_transcription",
    "self_argv",
    "self_command",
]
