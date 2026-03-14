"""Entry point for `python -m claude_compaction_viewer` and the `ccv` CLI command."""

import sys

# Windows defaults stdout to the system ANSI codepage (e.g. cp1252) even when
# the terminal supports UTF-8.  Force UTF-8 so Unicode box-drawing / emoji work.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


HELP = """\
Claude Code Compaction Viewer

Usage:
    ccv                     Launch interactive TUI
    ccv --scan              Scan all projects, show compaction summary table
    ccv --summary <file>    Print compaction summaries from a JSONL file
    ccv <file.jsonl>        Open TUI with file pre-loaded
    ccv --help              Show this help

TUI Keybindings:
    c / C       Jump to next / previous compaction boundary
    t           Toggle progress messages
    s           Show all compaction summaries in detail panel
    j / k       Scroll down / up
    q           Quit
"""


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(HELP)
        return

    if "--scan" in args:
        from .cli import cli_scan
        cli_scan()
        return

    if "--summary" in args:
        idx = args.index("--summary")
        if idx + 1 < len(args):
            from .cli import cli_summary
            cli_summary(args[idx + 1])
        else:
            print("Usage: ccv --summary <file.jsonl>")
            sys.exit(1)
        return

    filepath = None
    for a in args:
        if not a.startswith("-"):
            filepath = a
            break

    from .tui import CompactionViewer
    app = CompactionViewer(initial_file=filepath)
    app.run()


if __name__ == "__main__":
    main()
