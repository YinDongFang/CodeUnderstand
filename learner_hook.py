#!/usr/bin/env python3
"""
Claude Code Stop hook for the Learner pane: after each assistant reply, inject the
message into the Reader pane via tmux-bridge.

Configure in the Learner project .claude/settings.json:
  "Stop": [{ "type": "command", "command": "python3 <absolute-path>/learner_hook.py" }]
"""

import sys

from relay_stop import forward_last_assistant_to_pane

if __name__ == "__main__":
    sys.exit(forward_last_assistant_to_pane("reader"))
