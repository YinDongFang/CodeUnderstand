#!/usr/bin/env python3
"""
Claude Code Stop hook for the Reader pane: after each assistant reply, inject the
message into the Learner pane via tmux-bridge.

Configure in the Reader project .claude/settings.json:
  "Stop": [{ "type": "command", "command": "python3 <absolute-path>/reader_hook.py" }]
"""

import sys

from relay_stop import forward_last_assistant_to_pane

if __name__ == "__main__":
    sys.exit(forward_last_assistant_to_pane("learner"))
