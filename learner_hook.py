#!/usr/bin/env python3
"""
Claude Code Stop hook for the Learner pane: after each assistant reply, inject the
message into the Reader pane via tmux-bridge.

Configure in the Learner project .claude/settings.json:
  "Stop": [{ "type": "command", "command": "python3 <absolute-path>/learner_hook.py" }]
"""

import sys

from relay_stop import relay_stop_forward

if __name__ == "__main__":
    sys.exit(relay_stop_forward("reader"))
