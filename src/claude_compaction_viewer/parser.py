"""Parse Claude Code JSONL conversation files and extract compaction events."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


CLAUDE_DIR = Path.home() / ".claude" / "projects"


@dataclass
class CompactEvent:
    line_idx: int
    trigger: str
    pre_tokens: int
    timestamp: str
    summary_line_idx: int
    summary_text: str
    summary_length: int


@dataclass
class ConversationStats:
    total_messages: int = 0
    user_messages: int = 0
    assistant_messages: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    progress_messages: int = 0
    system_messages: int = 0
    file_snapshots: int = 0
    compactions: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read: int = 0
    total_cache_create: int = 0
    models_used: set = field(default_factory=set)
    first_timestamp: str = ""
    last_timestamp: str = ""
    version: str = ""
    cwd: str = ""
    git_branch: str = ""
    slug: str = ""


@dataclass
class ParsedMessage:
    line_idx: int
    msg_type: str
    role: str
    timestamp: str
    content_preview: str
    content_full: str
    model: str
    usage: dict
    is_compact_boundary: bool
    is_compact_summary: bool
    compact_metadata: dict
    tool_name: str
    uuid: str


def parse_jsonl(filepath: str) -> tuple[list[ParsedMessage], list[CompactEvent], ConversationStats]:
    messages = []
    compactions = []
    stats = ConversationStats()

    with open(filepath, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    stats.total_messages = len(lines)

    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = obj.get("type", "unknown")
        msg = obj.get("message", {})
        role = msg.get("role", msg_type)
        model = msg.get("model", "")
        usage = msg.get("usage", {})
        timestamp = obj.get("timestamp", "")
        uuid = obj.get("uuid", "")
        is_boundary = obj.get("subtype") == "compact_boundary"
        is_summary = bool(obj.get("isCompactSummary"))
        compact_meta = obj.get("compactMetadata", {})
        tool_name = ""

        if timestamp:
            if not stats.first_timestamp:
                stats.first_timestamp = timestamp
            stats.last_timestamp = timestamp

        if obj.get("version"):
            stats.version = obj["version"]
        if obj.get("cwd"):
            stats.cwd = obj["cwd"]
        if obj.get("gitBranch"):
            stats.git_branch = obj["gitBranch"]
        if obj.get("slug"):
            stats.slug = obj["slug"]

        if model:
            stats.models_used.add(model)

        stats.total_input_tokens += usage.get("input_tokens", 0)
        stats.total_output_tokens += usage.get("output_tokens", 0)
        stats.total_cache_read += usage.get("cache_read_input_tokens", 0)
        stats.total_cache_create += usage.get("cache_creation_input_tokens", 0)

        content = msg.get("content", obj.get("content", ""))
        content_full = ""
        content_preview = ""
        content_types = set()

        if isinstance(content, str):
            content_full = content
            content_preview = content[:120].replace("\n", " ")
        elif isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict):
                    ctype = c.get("type", "")
                    content_types.add(ctype)
                    if ctype == "text":
                        parts.append(c.get("text", ""))
                    elif ctype == "thinking":
                        parts.append(f"[thinking] {c.get('thinking', '')}")
                    elif ctype == "tool_use":
                        tn = c.get("name", "unknown")
                        tool_name = tn
                        inp = json.dumps(c.get("input", {}))
                        if len(inp) > 200:
                            inp = inp[:200] + "..."
                        parts.append(f"[tool_use: {tn}] {inp}")
                    elif ctype == "tool_result":
                        tid = c.get("tool_use_id", "")[:12]
                        tc = c.get("content", "")
                        if isinstance(tc, list):
                            tc = str(tc)
                        if isinstance(tc, str) and len(tc) > 200:
                            tc = tc[:200] + "..."
                        parts.append(f"[tool_result: {tid}] {tc}")
                    else:
                        parts.append(f"[{ctype}] {str(c)[:100]}")
            content_full = "\n\n".join(parts)
            content_preview = content_full[:120].replace("\n", " ")

        if msg_type == "user":
            if content_types == {"tool_result"}:
                msg_type = "tool_result"
                stats.tool_results += 1
            else:
                stats.user_messages += 1
        elif msg_type == "assistant":
            if content_types == {"tool_use"}:
                msg_type = "tool_use"
                stats.tool_calls += 1
            else:
                stats.assistant_messages += 1
                if tool_name:
                    stats.tool_calls += 1
        elif msg_type == "progress":
            stats.progress_messages += 1
            data = obj.get("data", {})
            dtype = data.get("type", "")
            content_preview = f"[{dtype}] {data.get('toolName', data.get('command', ''))}"
            content_full = json.dumps(data, indent=2)
        elif msg_type == "system":
            stats.system_messages += 1
        elif msg_type == "file-history-snapshot":
            stats.file_snapshots += 1

        if is_boundary:
            stats.compactions += 1

        parsed = ParsedMessage(
            line_idx=i,
            msg_type=msg_type,
            role=role,
            timestamp=timestamp,
            content_preview=content_preview,
            content_full=content_full,
            model=model,
            usage=usage,
            is_compact_boundary=is_boundary,
            is_compact_summary=is_summary,
            compact_metadata=compact_meta,
            tool_name=tool_name,
            uuid=uuid,
        )
        messages.append(parsed)

        if is_boundary:
            compactions.append(CompactEvent(
                line_idx=i,
                trigger=compact_meta.get("trigger", ""),
                pre_tokens=compact_meta.get("preTokens", 0),
                timestamp=timestamp,
                summary_line_idx=-1,
                summary_text="",
                summary_length=0,
            ))

        if is_summary and compactions:
            compactions[-1].summary_line_idx = i
            compactions[-1].summary_text = content_full
            compactions[-1].summary_length = len(content_full)

    return messages, compactions, stats


def humanize_project_name(dirname: str) -> str:
    """Convert a Claude Code project dirname like '-Users-foo-Work-bar' to '~/Work/bar'."""
    home_user = os.environ.get("USER", os.environ.get("USERNAME", ""))
    name = dirname
    if home_user:
        name = name.replace(f"-Users-{home_user}-", "~/")
    name = name.replace("-", "/")
    if name.startswith("/"):
        name = "~" + name
    return name
