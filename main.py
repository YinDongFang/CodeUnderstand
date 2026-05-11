"""
Claude Code 会话去重：单文件入口。

通过命令行传入的 session_id（即主会话 .jsonl 文件名不含扩展名）在
~/.claude/projects 下定位会话文件，检测重复用户提问并删除较晚出现的轮次。
"""

import argparse
import json
import os
from difflib import SequenceMatcher
from dataclasses import dataclass, field, asdict
from typing import Optional

def get_projects_dir() -> str:
    """Claude Code 默认项目根目录 ~/.claude/projects。"""
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


def compute_later_duplicate_turn_indices(turns: list) -> list:
    """
    基于 parse 阶段写入的 similar_to：若某轮与更早的轮次相似，则删除该轮（保留首次出现）。
    """
    to_delete: set = set()
    for turn in turns:
        for sim in turn.similar_to:
            other = sim.get("turn_index")
            if isinstance(other, int) and other < turn.turn_index:
                to_delete.add(turn.turn_index)
    return sorted(to_delete)


def run_dedupe_session(session_id: str) -> dict:
    """
    根据 session_id 在 ~/.claude/projects 下查找主会话 .jsonl 并删除重复轮次。

    Returns:
        摘要 dict：路径、删除数量等。
    """
    sid = (session_id or "").strip()
    if not sid:
        raise ValueError("session_id 不能为空")

    found = find_session_by_id(sid)
    main_file = found["main_file"]
    sub_files = found["subagent_files"]
    project_dir = found["parent_dir"]
    parsed = parse_main_jsonl(main_file, sub_files)
    turn_indices = compute_later_duplicate_turn_indices(parsed.turns)
    if not turn_indices:
        return {
            "ok": True,
            "message": "未检测到需删除的重复对话轮次",
            "project_dir": project_dir,
            "main_file": main_file,
            "session_id": sid,
            "deleted": 0,
        }
    session_slug = os.path.basename(project_dir)
    ok = delete_turns(main_file, turn_indices, parsed)
    delete_turns_from_subagents(main_file, turn_indices, parsed, {})
    remaining = len(parsed.turns) - len(turn_indices)
    return {
        "ok": ok,
        "message": f"已删除 {len(turn_indices)} 个重复轮次，剩余 {remaining} 轮",
        "project_dir": project_dir,
        "main_file": main_file,
        "session_id": sid,
        "session_slug": session_slug,
        "deleted": len(turn_indices),
        "remaining_turns": remaining,
        "turn_indices": turn_indices,
    }


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SubagentTurn:
    """A message inside a subagent conversation."""
    role: str              # "user" | "assistant"
    text: str              # readable text content
    tool_name: Optional[str] = None      # tool name if tool_use
    tool_input: Optional[str] = None     # tool input summary
    tool_result: Optional[str] = None    # tool result summary
    line_indices: list = field(default_factory=list)  # line numbers in subagent JSONL


@dataclass
class SubagentConversation:
    """A full subagent execution trace."""
    file_path: str
    agent_id: str
    description: str
    turns: list = field(default_factory=list)  # list of SubagentTurn


@dataclass
class DialogTurn:
    """
    One conversation turn = user question + assistant answer.
    Also tracks the line indices in the main JSONL so we can delete/write-back.
    """
    turn_index: int
    user_text: str
    assistant_texts: list = field(default_factory=list)   # assistant text blocks
    thinking_texts: list = field(default_factory=list)     # thinking blocks
    tool_uses: list = field(default_factory=list)          # [{name, input_summary}]
    subagents: list = field(default_factory=list)          # list of SubagentConversation
    # Line indices in the main JSONL that belong to this turn
    line_indices: list = field(default_factory=list)
    timestamp: Optional[str] = None
    uuid: Optional[str] = None  # uuid of the user message (turn anchor)
    # Similarity detection: list of {"turn_index": int, "ratio": float}
    similar_to: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        return d


@dataclass
class ParsedSession:
    """Parsed result for one JSONL file."""
    file_path: str
    session_id: str
    slug: str
    version: str
    cwd: str
    turns: list = field(default_factory=list)  # list of DialogTurn

    def to_dict(self):
        return {
            "file_path": self.file_path,
            "session_id": self.session_id,
            "slug": self.slug,
            "version": self.version,
            "cwd": self.cwd,
            "turns": [t.to_dict() for t in self.turns],
        }


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def discover_session_files(base_dir: str, session_id: str) -> dict:
    """
    Given base_dir (e.g. ~/.claude/projects) and session_id,
    find the session directory and return all JSONL files.

    Returns:
        {
            "session_dir": str,
            "main_files": [str, ...],         # top-level .jsonl files
            "subagent_files": {
                "main_file_stem": [str, ...]   # subagent .jsonl files
            }
        }
    """
    session_dir = os.path.join(base_dir, session_id)
    if not os.path.isdir(session_dir):
        raise FileNotFoundError(f"Session directory not found: {session_dir}")

    result = {
        "session_dir": session_dir,
        "main_files": [],
        "subagent_files": {},
    }

    # Find top-level JSONL files
    for f in sorted(os.listdir(session_dir)):
        if f.endswith(".jsonl"):
            full_path = os.path.join(session_dir, f)
            result["main_files"].append(full_path)

            # Check for corresponding subdirectory with subagents
            stem = f[:-6]  # remove .jsonl
            subagent_dir = os.path.join(session_dir, stem, "subagents")
            if os.path.isdir(subagent_dir):
                sub_files = []
                for sf in sorted(os.listdir(subagent_dir)):
                    if sf.endswith(".jsonl"):
                        sub_files.append(os.path.join(subagent_dir, sf))
                if sub_files:
                    result["subagent_files"][full_path] = sub_files

    return result


# ---------------------------------------------------------------------------
# JSONL parsing helpers
# ---------------------------------------------------------------------------

def _load_jsonl(file_path: str) -> list:
    """Load all JSON lines from a JSONL file."""
    lines = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    lines.append(None)
    return lines


def _extract_text_from_content(content) -> str:
    """Extract readable text from message content (string or list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    texts.append(item.get("text", ""))
        return "\n".join(texts)
    return ""


def _extract_thinking_from_content(content) -> str:
    """Extract thinking text from message content."""
    if not isinstance(content, list):
        return ""
    texts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "thinking":
            texts.append(item.get("thinking", ""))
    return "\n".join(texts)


def _extract_tool_uses_from_content(content) -> list:
    """Extract tool_use info from message content."""
    if not isinstance(content, list):
        return []
    tools = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            inp = item.get("input", {})
            # Summarize input
            if isinstance(inp, dict):
                if "command" in inp:
                    input_summary = inp["command"][:200]
                elif "file_path" in inp:
                    input_summary = inp["file_path"]
                elif "prompt" in inp:
                    input_summary = inp["prompt"][:200]
                else:
                    input_summary = json.dumps(inp, ensure_ascii=False)[:200]
            else:
                input_summary = str(inp)[:200]
            tools.append({
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "input_summary": input_summary,
            })
    return tools


def _extract_tool_results_from_content(content) -> list:
    """Extract tool_result info from message content (user messages with tool results)."""
    if not isinstance(content, list):
        return []
    results = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_result":
            result_content = item.get("content", "")
            if isinstance(result_content, str):
                results.append({
                    "tool_use_id": item.get("tool_use_id", ""),
                    "content": result_content[:500],
                    "is_error": item.get("is_error", False),
                })
    return results


# ---------------------------------------------------------------------------
# Subagent parsing
# ---------------------------------------------------------------------------

def parse_subagent_file(file_path: str) -> SubagentConversation:
    """Parse a subagent JSONL file into a SubagentConversation."""
    lines = _load_jsonl(file_path)
    turns = []
    agent_id = ""
    description = os.path.basename(file_path)

    for i, obj in enumerate(lines):
        if obj is None:
            continue
        msg_type = obj.get("type", "")
        if not agent_id:
            agent_id = obj.get("agentId", "")

        if msg_type == "user":
            msg = obj.get("message", {})
            content = msg.get("content", "")
            text = _extract_text_from_content(content)
            tool_results = _extract_tool_results_from_content(content)
            if text:
                turns.append(SubagentTurn(role="user", text=text, line_indices=[i]))
            elif tool_results:
                for tr in tool_results:
                    turns.append(SubagentTurn(
                        role="user",
                        text="",
                        tool_result=tr["content"],
                        line_indices=[i],
                    ))

        elif msg_type == "assistant":
            msg = obj.get("message", {})
            content = msg.get("content", [])
            text = _extract_text_from_content(content)
            tool_uses = _extract_tool_uses_from_content(content)
            if text:
                turns.append(SubagentTurn(role="assistant", text=text, line_indices=[i]))
            for tu in tool_uses:
                turns.append(SubagentTurn(
                    role="assistant",
                    text="",
                    tool_name=tu["name"],
                    tool_input=tu["input_summary"],
                    line_indices=[i],
                ))

    return SubagentConversation(
        file_path=file_path,
        agent_id=agent_id,
        description=description,
        turns=turns,
    )


# ---------------------------------------------------------------------------
# Main JSONL parsing - extract dialog turns
# ---------------------------------------------------------------------------

def parse_main_jsonl(file_path: str, subagent_files: list = None) -> ParsedSession:
    """
    Parse a main Claude Code JSONL file and extract dialog turns.

    Each "turn" is anchored by a user message with type="user" and string content
    (i.e. an actual user question, not a tool_result).
    All subsequent assistant messages, progress, system messages etc. until the next
    user question belong to that turn.
    """
    lines = _load_jsonl(file_path)

    # Extract metadata from first user message
    session_id = ""
    slug = ""
    version = ""
    cwd = ""

    # First pass: identify user question indices
    user_question_indices = []
    for i, obj in enumerate(lines):
        if obj is None:
            continue
        if obj.get("type") == "user":
            msg = obj.get("message", {})
            content = msg.get("content", "")
            # A real user question has string content (not list with tool_results)
            if isinstance(content, str) and content.strip():
                # Skip system-injected messages (not real user input)
                stripped = content.strip()
                if stripped.startswith("<task-notification>"):
                    continue
                user_question_indices.append(i)
                if not session_id:
                    session_id = obj.get("sessionId", "")
                    slug = obj.get("slug", "")
                    version = obj.get("version", "")
                    cwd = obj.get("cwd", "")

    # Second pass: build turns
    turns = []
    for turn_idx, uq_idx in enumerate(user_question_indices):
        # Determine the range of lines belonging to this turn
        next_uq_idx = (
            user_question_indices[turn_idx + 1]
            if turn_idx + 1 < len(user_question_indices)
            else len(lines)
        )

        user_obj = lines[uq_idx]
        user_msg = user_obj.get("message", {})
        user_text = user_msg.get("content", "")

        turn = DialogTurn(
            turn_index=turn_idx,
            user_text=user_text,
            timestamp=user_obj.get("timestamp"),
            uuid=user_obj.get("uuid"),
            line_indices=list(range(uq_idx, next_uq_idx)),
        )

        # Also include any lines before the first user question (metadata, snapshots)
        if turn_idx == 0 and uq_idx > 0:
            turn.line_indices = list(range(0, next_uq_idx))

        # Collect assistant content, tool uses, subagent agentIds from this range
        turn_agent_ids = set()  # agentIds seen in progress messages of this turn

        for li in range(uq_idx + 1, next_uq_idx):
            obj = lines[li]
            if obj is None:
                continue
            obj_type = obj.get("type", "")

            if obj_type == "assistant":
                msg = obj.get("message", {})
                content = msg.get("content", [])
                text = _extract_text_from_content(content)
                thinking = _extract_thinking_from_content(content)
                tools = _extract_tool_uses_from_content(content)
                if text:
                    turn.assistant_texts.append(text)
                if thinking:
                    turn.thinking_texts.append(thinking)
                for t in tools:
                    turn.tool_uses.append(t)

            elif obj_type == "progress":
                # Extract agentId from progress messages for subagent matching
                data = obj.get("data", {})
                aid = data.get("agentId", "")
                if aid:
                    turn_agent_ids.add(aid)

        turn._agent_ids = turn_agent_ids
        turns.append(turn)

    # Parse subagent files and associate with turns by agentId
    if subagent_files:
        # Build agentId -> SubagentConversation map
        agent_id_to_convo = {}
        for sf in subagent_files:
            convo = parse_subagent_file(sf)
            if convo.agent_id:
                agent_id_to_convo[convo.agent_id] = convo

        # Match each turn's agentIds to subagent conversations
        for turn in turns:
            for aid in getattr(turn, "_agent_ids", set()):
                if aid in agent_id_to_convo:
                    convo = agent_id_to_convo[aid]
                    if convo not in turn.subagents:
                        turn.subagents.append(convo)

    # Detect similar user questions within the same file
    _detect_similar_turns(turns)

    return ParsedSession(
        file_path=file_path,
        session_id=session_id,
        slug=slug,
        version=version,
        cwd=cwd,
        turns=turns,
    )


# ---------------------------------------------------------------------------
# Similarity detection
# ---------------------------------------------------------------------------

SIMILARITY_THRESHOLD = 0.85


def _detect_similar_turns(turns: list, threshold: float = SIMILARITY_THRESHOLD):
    """
    Compare all user_text pairs within turns. If similarity >= threshold,
    mark both turns with each other's index and the ratio.
    Uses difflib.SequenceMatcher (no external dependencies).
    """
    n = len(turns)
    if n < 2:
        return

    for i in range(n):
        for j in range(i + 1, n):
            text_i = turns[i].user_text
            text_j = turns[j].user_text

            # Skip very short texts (less than 20 chars) to avoid false positives
            if len(text_i) < 20 or len(text_j) < 20:
                continue

            ratio = SequenceMatcher(None, text_i, text_j).ratio()
            if ratio >= threshold:
                turns[i].similar_to.append({
                    "turn_index": turns[j].turn_index,
                    "ratio": round(ratio, 4),
                })
                turns[j].similar_to.append({
                    "turn_index": turns[i].turn_index,
                    "ratio": round(ratio, 4),
                })


# ---------------------------------------------------------------------------
# Deletion and write-back
# ---------------------------------------------------------------------------

def delete_turns(file_path: str, turn_indices: list, parsed: ParsedSession) -> bool:
    """
    Delete specified turns from the JSONL file.

    Args:
        file_path: Path to the main JSONL file
        turn_indices: List of turn indices to delete
        parsed: The ParsedSession (used to find line ranges)

    Returns:
        True if successful
    """
    # Collect all line indices to remove
    lines_to_remove = set()
    for turn in parsed.turns:
        if turn.turn_index in turn_indices:
            lines_to_remove.update(turn.line_indices)

    if not lines_to_remove:
        return False

    # Read original file
    with open(file_path, "r", encoding="utf-8") as f:
        all_lines = f.readlines()

    # Write back without deleted lines
    with open(file_path, "w", encoding="utf-8") as f:
        for i, line in enumerate(all_lines):
            if i not in lines_to_remove:
                f.write(line)

    return True


def delete_turns_from_subagents(
    main_file_path: str,
    turn_indices: list,
    parsed: ParsedSession,
    subagent_files_map: dict,
):
    """
    When turns with subagent calls are deleted, also clean up the subagent files.
    For simplicity, if a turn that launched a subagent is deleted, we remove
    the entire subagent file (since the subagent is associated with that turn).
    """
    for turn in parsed.turns:
        if turn.turn_index in turn_indices:
            for sub in turn.subagents:
                if os.path.exists(sub.file_path):
                    os.remove(sub.file_path)


# ---------------------------------------------------------------------------
# File search: find JSONL by session ID (file stem)
# ---------------------------------------------------------------------------

def find_session_by_id(session_id: str) -> dict:
    """
    Recursively scan ~/.claude/projects to find JSONL files whose
    filename (without .jsonl) matches the given session_id.

    Also collects subagent files from the corresponding subdirectory.

    Returns:
        {
            "main_file": str,              # full path to the matched .jsonl
            "subagent_files": [str, ...],  # subagent .jsonl files
            "parent_dir": str,             # the directory containing the file
        }
    Raises FileNotFoundError if no match found.
    """
    target_name = session_id + ".jsonl"

    projects_dir = get_projects_dir()
    for root, dirs, files in os.walk(projects_dir):
        # Skip subagents directories during top-level scan
        if "subagents" in root.split(os.sep):
            continue
        for f in files:
            if f == target_name:
                main_file = os.path.join(root, f)
                sub_files = []

                # Pattern 1: <root>/<stem>/subagents/*.jsonl
                sub_dir1 = os.path.join(root, session_id, "subagents")
                if os.path.isdir(sub_dir1):
                    for sf in sorted(os.listdir(sub_dir1)):
                        if sf.endswith(".jsonl"):
                            sub_files.append(os.path.join(sub_dir1, sf))

                # Pattern 2: <root>/subagents/*.jsonl (direct sibling)
                sub_dir2 = os.path.join(root, "subagents")
                if os.path.isdir(sub_dir2) and sub_dir2 != sub_dir1:
                    for sf in sorted(os.listdir(sub_dir2)):
                        if sf.endswith(".jsonl"):
                            sub_files.append(os.path.join(sub_dir2, sf))

                return {
                    "main_file": main_file,
                    "subagent_files": sub_files,
                    "parent_dir": root,
                }

    raise FileNotFoundError(
        f"未找到匹配的文件: {target_name}\n扫描目录: {projects_dir}"
    )


def list_all_sessions() -> list:
    """
    Scan ~/.claude/projects recursively and list all JSONL files
    (excluding those inside subagents/ directories).

    Returns list of:
        {"session_id": str, "file_path": str, "parent_dir": str}
    """
    results = []
    projects_dir = get_projects_dir()
    if not os.path.isdir(projects_dir):
        return results

    for root, dirs, files in os.walk(projects_dir):
        # Skip subagents directories
        parts = root.split(os.sep)
        if "subagents" in parts:
            continue
        for f in sorted(files):
            if f.endswith(".jsonl"):
                stem = f[:-6]
                results.append({
                    "session_id": stem,
                    "file_path": os.path.join(root, f),
                    "parent_dir": root,
                })
    return results


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------

def parse_session(base_dir: str, session_id: str) -> list:
    """
    Parse all JSONL files in a session directory.

    Args:
        base_dir: Base directory (e.g. ~/.claude/projects)
        session_id: Session identifier (directory name)

    Returns:
        List of ParsedSession objects
    """
    discovery = discover_session_files(base_dir, session_id)
    results = []

    for main_file in discovery["main_files"]:
        sub_files = discovery["subagent_files"].get(main_file, [])
        parsed = parse_main_jsonl(main_file, sub_files)
        results.append(parsed)

    return results


def parse_by_session_id(session_id: str) -> ParsedSession:
    """
    Find and parse a single JSONL file by its session ID (filename stem).
    Automatically scans ~/.claude/projects.

    Args:
        session_id: The JSONL filename without extension
                    (e.g. "5b99f3f4-5de5-45b4-b442-110a8d5d12a2")

    Returns:
        ParsedSession
    """
    found = find_session_by_id(session_id)
    parsed = parse_main_jsonl(found["main_file"], found["subagent_files"])
    return parsed


def delete_by_session_id(session_id: str, turn_indices: list) -> dict:
    """
    Delete specified turns from a JSONL file found by session_id.

    Returns:
        {"success": bool, "deleted_turns": int, "remaining_turns": int}
    """
    found = find_session_by_id(session_id)
    parsed = parse_main_jsonl(found["main_file"], found["subagent_files"])

    success = delete_turns(found["main_file"], turn_indices, parsed)

    delete_turns_from_subagents(
        found["main_file"], turn_indices, parsed, {}
    )

    remaining = len(parsed.turns) - len(turn_indices)
    return {
        "success": success,
        "deleted_turns": len(turn_indices),
        "remaining_turns": remaining,
    }


def delete_session_turns(
    base_dir: str,
    session_id: str,
    file_path: str,
    turn_indices: list,
) -> dict:
    """
    Delete specified turns from a session file and write back.

    Args:
        base_dir: Base directory
        session_id: Session identifier
        file_path: Path to the specific JSONL file
        turn_indices: List of turn indices to delete

    Returns:
        {"success": bool, "deleted_turns": int, "remaining_turns": int}
    """
    discovery = discover_session_files(base_dir, session_id)
    sub_files = discovery["subagent_files"].get(file_path, [])
    parsed = parse_main_jsonl(file_path, sub_files)

    # Delete from main file
    success = delete_turns(file_path, turn_indices, parsed)

    # Clean up subagent files
    delete_turns_from_subagents(file_path, turn_indices, parsed, discovery["subagent_files"])

    remaining = len(parsed.turns) - len(turn_indices)

    return {
        "success": success,
        "deleted_turns": len(turn_indices),
        "remaining_turns": remaining,
    }


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="根据 session_id 定位会话 JSONL 并清理其中的重复对话轮次"
    )
    ap.add_argument(
        "session_id",
        help="会话 ID：主会话文件名主名（不含 .jsonl），与 ~/.claude/projects 下文件一致",
    )
    args = ap.parse_args()
    session_id = (args.session_id or "").strip()
    if not session_id:
        ap.error("session_id 不能为空")
    try:
        summary = run_dedupe_session(session_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"错误: {e}")
        raise SystemExit(1)
    except Exception as e:
        print(f"错误: {e}")
        raise SystemExit(1)
    print(summary.get("message", ""))
    print(f"项目目录: {summary.get('project_dir', '')}")
    print(f"会话文件: {summary.get('main_file', '')}")
    if summary.get("deleted", 0):
        print(f"已删除轮次索引: {summary.get('turn_indices', [])}")
