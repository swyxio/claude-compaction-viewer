"""Interactive TUI for browsing Claude Code conversation history and compaction events."""

from datetime import datetime

from textual.app import App
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    RichLog,
    Static,
    Tree,
)

from .parser import (
    CLAUDE_DIR,
    CompactEvent,
    ConversationStats,
    ParsedMessage,
    humanize_project_name,
    parse_jsonl,
)
from .fmt import format_timestamp, format_tokens, format_duration


TUI_TYPE_ICONS = {
    "user": "\U0001f464",
    "assistant": "\U0001f916",
    "system": "\u2699\ufe0f",
    "progress": "\u23f3",
    "tool_use": "\U0001f527",
    "tool_result": "\U0001f527",
    "file-history-snapshot": "\U0001f4f8",
}

ROLE_COLORS = {
    "user": "bold cyan",
    "assistant": "bold green",
    "system": "bold yellow",
    "progress": "dim",
    "tool_use": "dim cyan",
    "tool_result": "dim cyan",
    "file-history-snapshot": "dim magenta",
}


class ProjectTree(Tree):
    def __init__(self):
        super().__init__("~/.claude/projects", id="project-tree")
        self.guide_depth = 3

    def on_mount(self) -> None:
        self.root.expand()
        if not CLAUDE_DIR.exists():
            self.root.add_leaf("(no ~/.claude/projects found)")
            return

        for project_dir in sorted(CLAUDE_DIR.iterdir()):
            if not project_dir.is_dir():
                continue
            display_name = humanize_project_name(project_dir.name)
            node = self.root.add(display_name, data=str(project_dir))
            jsonl_files = sorted(
                project_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for jf in jsonl_files:
                size = jf.stat().st_size
                if size > 1_000_000:
                    size_str = f"{size/1_000_000:.1f}MB"
                elif size > 1000:
                    size_str = f"{size/1000:.0f}KB"
                else:
                    size_str = f"{size}B"
                mtime = datetime.fromtimestamp(jf.stat().st_mtime).strftime("%m/%d %H:%M")
                node.add_leaf(f"{jf.stem[:8]}\u2026 {size_str} {mtime}", data=str(jf))


class CompactionViewer(App):
    TITLE = "Claude Code Compaction Viewer"
    CSS = """
    #main-layout {
        height: 1fr;
    }
    #sidebar {
        width: 38;
        border-right: solid $primary-darken-2;
    }
    #project-tree {
        height: 1fr;
    }
    #content-area {
        width: 1fr;
    }
    #stats-bar {
        height: 3;
        padding: 0 1;
        background: $surface;
        border-bottom: solid $primary-darken-2;
    }
    #stats-bar Label {
        margin-right: 2;
    }
    #message-table {
        height: 1fr;
        max-height: 50%;
    }
    #detail-panel {
        height: 1fr;
        border-top: solid $primary-darken-2;
    }
    #detail-log {
        height: 1fr;
    }
    #compaction-bar {
        height: auto;
        max-height: 6;
        padding: 0 1;
        background: $warning 15%;
        border-bottom: solid $warning;
        display: none;
    }
    #compaction-bar.has-compactions {
        display: block;
    }
    #no-file {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        text-align: center;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("c", "jump_compaction", "Next Compaction"),
        Binding("shift+c", "jump_compaction_prev", "Prev Compaction"),
        Binding("t", "toggle_progress", "Toggle Progress"),
        Binding("s", "show_summary", "Show Summary"),
    ]

    show_progress_msgs = reactive(False)
    current_file = reactive("")

    def __init__(self, initial_file: str | None = None):
        super().__init__()
        self._initial_file = initial_file
        self.messages: list[ParsedMessage] = []
        self.compactions: list[CompactEvent] = []
        self.stats = ConversationStats()
        self._compaction_rows: list[int] = []

    def compose(self):
        yield Header(show_clock=True)
        with Horizontal(id="main-layout"):
            with Vertical(id="sidebar"):
                yield ProjectTree()
            with Vertical(id="content-area"):
                yield Label("Select a JSONL file from the tree \u2192", id="no-file")
                yield Horizontal(id="stats-bar")
                yield Static(id="compaction-bar")
                yield DataTable(id="message-table", cursor_type="row")
                with VerticalScroll(id="detail-panel"):
                    yield RichLog(id="detail-log", highlight=True, markup=True, wrap=True)
        yield Footer()

    def on_mount(self):
        table = self.query_one("#message-table", DataTable)
        table.add_columns("#", "Time", "Type", "Role/Tool", "Tokens", "Preview")
        table.display = False
        self.query_one("#stats-bar").display = False
        self.query_one("#detail-panel").display = False
        if self._initial_file:
            self.call_later(lambda: self.load_file(self._initial_file))

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        node = event.node
        if node.data and str(node.data).endswith(".jsonl"):
            self.load_file(node.data)

    def load_file(self, filepath: str) -> None:
        self.current_file = filepath
        self.messages, self.compactions, self.stats = parse_jsonl(filepath)

        self.query_one("#no-file").display = False
        self.query_one("#stats-bar").display = True
        self.query_one("#detail-panel").display = True

        table = self.query_one("#message-table", DataTable)
        table.display = True
        table.clear()

        stats_bar = self.query_one("#stats-bar", Horizontal)
        stats_bar.remove_children()
        s = self.stats
        duration = format_duration(s.first_timestamp, s.last_timestamp)

        stats_bar.mount(Label(f"[bold]msgs:[/] {s.total_messages}"))
        stats_bar.mount(Label(f"[cyan]user:[/] {s.user_messages}"))
        stats_bar.mount(Label(f"[green]asst:[/] {s.assistant_messages}"))
        stats_bar.mount(Label(f"[yellow]sys:[/] {s.system_messages}"))
        stats_bar.mount(Label(f"[magenta]tools:[/] {s.tool_calls}"))
        stats_bar.mount(Label(f"[bold red]compactions:[/] {s.compactions}"))
        stats_bar.mount(Label(f"[dim]in:[/] {format_tokens(s.total_input_tokens)}"))
        stats_bar.mount(Label(f"[dim]out:[/] {format_tokens(s.total_output_tokens)}"))
        stats_bar.mount(Label(f"[dim]cache_r:[/] {format_tokens(s.total_cache_read)}"))
        if duration:
            stats_bar.mount(Label(f"[dim]dur:[/] {duration}"))
        if s.models_used:
            stats_bar.mount(Label(f"[dim]model:[/] {', '.join(s.models_used)}"))

        comp_bar = self.query_one("#compaction-bar", Static)
        if self.compactions:
            comp_bar.add_class("has-compactions")
            lines = []
            for ci, ce in enumerate(self.compactions):
                lines.append(
                    f"  \u26a1 Compaction {ci+1}: line {ce.line_idx}, "
                    f"trigger={ce.trigger}, pre_tokens={format_tokens(ce.pre_tokens)}, "
                    f"summary={format_tokens(ce.summary_length)} chars @ {format_timestamp(ce.timestamp)}"
                )
            comp_bar.update("\n".join(lines))
        else:
            comp_bar.remove_class("has-compactions")
            comp_bar.update("")

        self._compaction_rows = []
        row_idx = 0
        for msg in self.messages:
            if not self.show_progress_msgs and msg.msg_type in ("progress", "tool_use", "tool_result"):
                continue
            if msg.msg_type == "file-history-snapshot":
                continue

            icon = TUI_TYPE_ICONS.get(msg.msg_type, "  ")
            time_str = format_timestamp(msg.timestamp)

            role_or_tool = msg.role
            if msg.tool_name:
                role_or_tool = msg.tool_name

            tokens = ""
            if msg.usage:
                inp = msg.usage.get("input_tokens", 0)
                out = msg.usage.get("output_tokens", 0)
                if inp or out:
                    tokens = f"{format_tokens(inp)}\u2192{format_tokens(out)}"

            preview = msg.content_preview
            if msg.is_compact_boundary:
                preview = "\u2501\u2501\u2501 COMPACTION BOUNDARY \u2501\u2501\u2501"
                icon = "\u26a1"
            elif msg.is_compact_summary:
                preview = f"[SUMMARY: {len(msg.content_full)} chars] {preview}"
                icon = "\U0001f4cb"

            label = f"{icon} {msg.msg_type}"

            table.add_row(
                str(msg.line_idx),
                time_str,
                label,
                role_or_tool,
                tokens,
                preview[:100],
                key=str(msg.line_idx),
            )

            if msg.is_compact_boundary or msg.is_compact_summary:
                self._compaction_rows.append(row_idx)

            row_idx += 1

        log = self.query_one("#detail-log", RichLog)
        log.clear()
        log.write(f"[dim]Loaded {filepath}[/]")
        log.write(f"[dim]{len(self.messages)} messages, {len(self.compactions)} compactions[/]")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        line_idx = int(event.row_key.value)
        msg = None
        for m in self.messages:
            if m.line_idx == line_idx:
                msg = m
                break
        if not msg:
            return

        log = self.query_one("#detail-log", RichLog)
        log.clear()

        color = ROLE_COLORS.get(msg.msg_type, "white")
        log.write(f"[{color}]\u2501\u2501\u2501 {msg.msg_type.upper()} (line {msg.line_idx}) \u2501\u2501\u2501[/]")

        meta_parts = []
        if msg.timestamp:
            meta_parts.append(f"time={format_timestamp(msg.timestamp)}")
        if msg.model:
            meta_parts.append(f"model={msg.model}")
        if msg.uuid:
            meta_parts.append(f"uuid={msg.uuid[:12]}\u2026")
        if msg.tool_name:
            meta_parts.append(f"tool={msg.tool_name}")
        if meta_parts:
            log.write(f"[dim]{' | '.join(meta_parts)}[/]")

        if msg.usage:
            inp = msg.usage.get("input_tokens", 0)
            out = msg.usage.get("output_tokens", 0)
            cr = msg.usage.get("cache_read_input_tokens", 0)
            cc = msg.usage.get("cache_creation_input_tokens", 0)
            log.write(f"[dim]tokens: in={format_tokens(inp)} out={format_tokens(out)} cache_read={format_tokens(cr)} cache_create={format_tokens(cc)}[/]")

        if msg.is_compact_boundary:
            log.write("")
            log.write("[bold yellow]\u26a1 COMPACTION BOUNDARY[/]")
            log.write(f"[yellow]trigger: {msg.compact_metadata.get('trigger', 'unknown')}[/]")
            log.write(f"[yellow]pre-compaction tokens: {format_tokens(msg.compact_metadata.get('preTokens', 0))}[/]")
            log.write("")

        if msg.is_compact_summary:
            log.write("")
            log.write("[bold yellow]\U0001f4cb COMPACTION SUMMARY[/]")
            log.write(f"[yellow]length: {len(msg.content_full)} chars[/]")
            log.write("")

        log.write("")
        content = msg.content_full
        if len(content) > 10000:
            content = content[:10000] + f"\n\n[dim]\u2026 truncated ({len(msg.content_full)} total chars)[/]"
        if content:
            for line in content.split("\n"):
                log.write(line)
        else:
            log.write("[dim](no content)[/]")

    def action_toggle_progress(self) -> None:
        self.show_progress_msgs = not self.show_progress_msgs
        if self.current_file:
            self.load_file(self.current_file)

    def action_jump_compaction(self) -> None:
        if not self._compaction_rows:
            return
        table = self.query_one("#message-table", DataTable)
        current = table.cursor_row
        for r in self._compaction_rows:
            if r > current:
                table.move_cursor(row=r)
                return
        table.move_cursor(row=self._compaction_rows[0])

    def action_jump_compaction_prev(self) -> None:
        if not self._compaction_rows:
            return
        table = self.query_one("#message-table", DataTable)
        current = table.cursor_row
        for r in reversed(self._compaction_rows):
            if r < current:
                table.move_cursor(row=r)
                return
        table.move_cursor(row=self._compaction_rows[-1])

    def action_show_summary(self) -> None:
        if not self.compactions:
            return
        log = self.query_one("#detail-log", RichLog)
        log.clear()
        for ci, ce in enumerate(self.compactions):
            log.write(f"[bold yellow]\u2501\u2501\u2501 COMPACTION {ci+1} SUMMARY \u2501\u2501\u2501[/]")
            log.write(f"[dim]line: {ce.line_idx} | trigger: {ce.trigger} | pre_tokens: {format_tokens(ce.pre_tokens)} | timestamp: {ce.timestamp}[/]")
            log.write("")
            for line in ce.summary_text.split("\n"):
                log.write(line)
            log.write("")
            log.write("")
