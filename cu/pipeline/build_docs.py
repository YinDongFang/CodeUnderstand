"""会话复制归一化 + Claude 生成 ``doc/``。"""
from __future__ import annotations

import copy
import json
import os
import random
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from cu.logging_config import configure_logging
from cu.pipeline_env import resolve_outputs_dir, resolve_projects_dir

_MAX_CLAUDE_ATTEMPTS = 3

_log = logging.getLogger(__name__)


_OPENINGS = [
    "The codebase analysis is now complete. Please generate the full project documentation. Be comprehensive and technically precise.",
    "We have thoroughly examined this project. Produce a complete technical documentation set. Prioritize depth over brevity.",
    "Our code analysis has covered architecture, implementation, and usage patterns. Synthesize this into structured documentation.",
    "Having completed the codebase exploration, produce formal project documentation with sufficient detail for an unfamiliar engineer.",
    "This project has been analyzed in detail. Generate technical documentation covering architecture, implementation, and practical guidance.",
    "Based on the comprehensive code review performed in this session, produce a complete documentation package for this project.",
    "The analysis phase is complete. Please proceed to documentation generation with concrete references to the source code.",
    "We have established a detailed understanding of this project through systematic code exploration. Formalize that understanding into well-structured technical documents.",
]

_CLOSINGS = [
    "Begin writing all 4 files now.",
    "Proceed to generate all documentation files.",
    "Start producing the documentation immediately.",
    "Generate all files and write them to the doc/ directory now.",
    "Please create all documentation files at this time.",
]

_CORE = [
    "Write all output files into the `doc/` folder under the current working directory.",
    "Generate exactly 4 Markdown documents: overview.md, architecture.md, implementation.md, and a fourth determined by project type.",
    "All documentation must be written in English.",
    "Every document except overview.md must include exactly one Mermaid diagram with classDef coloring.",
    "Content must be grounded in what was actually discovered — do not fabricate details.",
]

_QUALITY = [
    "Reference concrete file paths and module names wherever relevant.",
    "Include key function signatures and class hierarchies.",
    "Describe data flows with specifics: input formats, transformations, output structures.",
    "Document non-obvious design decisions and trade-offs.",
    "Include short code snippets when they clarify a critical mechanism.",
    "List configuration options with default values and effects.",
    "Note error handling patterns, retry strategies, and edge cases.",
    "Use Markdown tables to organize structured information.",
    "Map out the initialization sequence and component wiring at startup.",
    "Identify key abstractions and how they compose to deliver functionality.",
    "Describe the dependency structure and how external libraries are integrated.",
    "Highlight patterns that help a new contributor understand the codebase quickly.",
]


def _build_out(msg: str) -> None:
    _log.info("%s", msg)


def _build_err(msg: str) -> None:
    _log.warning("%s", msg)


def _normalize_obj_inplace(o: dict) -> bool:
    changed = False
    msg = o.get("message")
    if isinstance(msg, dict) and "model" in msg and msg.get("model") != "model":
        msg["model"] = "model"
        changed = True
    data = o.get("data")
    if isinstance(data, dict):
        dm = data.get("message")
        if isinstance(dm, dict):
            inner = dm.get("message")
            if isinstance(inner, dict) and "model" in inner and inner.get("model") != "model":
                inner["model"] = "model"
                changed = True
    return changed


def normalize_session_jsonl(dst_jsonl: Path) -> int:
    tmp = dst_jsonl.with_suffix(dst_jsonl.suffix + ".tmp")
    changed_count = 0
    lines_out: list[str] = []
    raw_lines = dst_jsonl.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in raw_lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            _build_err("警告: 跳过无法解析的 JSONL 行")
            continue
        if not isinstance(obj, dict):
            lines_out.append(json.dumps(obj, ensure_ascii=False))
            continue
        x = copy.deepcopy(obj)
        if _normalize_obj_inplace(x):
            changed_count += 1
        lines_out.append(json.dumps(x, ensure_ascii=False))

    tmp.write_text("\n".join(lines_out) + ("\n" if lines_out else ""), encoding="utf-8")
    tmp.replace(dst_jsonl)
    return changed_count


def _build_doc_prompt(doc_abs_path: Path) -> str:
    opening = random.choice(_OPENINGS)
    closing = random.choice(_CLOSINGS)
    core_lines = list(_CORE)
    random.shuffle(core_lines)
    qn = random.randint(4, 6)
    qual_pick = random.sample(_QUALITY, min(qn, len(_QUALITY)))

    prompt = opening + "\n\nRequirements:"
    for i, line in enumerate(core_lines, start=1):
        prompt += f"\n{i}. {line}"
    prompt += "\n\nAdditionally, ensure the following where applicable:"
    for line in qual_pick:
        prompt += f"\n- {line}"
    prompt += f"\n\n{closing}"
    prompt += (
        "\n\nIMPORTANT: Write all doc/ output files to this absolute path: "
        f"{doc_abs_path}/ (not the current working directory)."
    )
    return prompt


def _maybe_stub_mock_docs(doc_abs_path: Path) -> None:
    stub_docs = ["overview.md", "architecture.md", "implementation.md", "deployment.md"]
    wrote = 0
    doc_abs_path.mkdir(parents=True, exist_ok=True)
    for fname in stub_docs:
        fp = doc_abs_path / fname
        if not fp.is_file():
            title = fname.replace(".md", "")
            fp.write_text(
                f"# {title} (mock)\n\n占位文档（BUILD_USE_MOCK_CLAUDE=1）。\n",
                encoding="utf-8",
            )
            wrote += 1
    if wrote:
        _build_out(f"已写入 {wrote} 个占位 doc/*.md（mock 模式）")


def run_build_docs(repo: str, session: str) -> None:
    env = os.environ.copy()
    outputs_dir = resolve_outputs_dir()
    projects_dir = resolve_projects_dir()

    repo_slug = repo.replace("_", "-")
    out_root = outputs_dir / f"code-understand-{repo}"
    project_dir = projects_dir / repo

    cpdir_raw = env.get("CLAUDE_PROJECT_DIR", "").strip()
    if cpdir_raw and Path(cpdir_raw).is_dir():
        claude_projects = Path(cpdir_raw)
    else:
        user = os.environ.get("USER", "unknown")
        claude_projects = (
            Path.home() / ".claude" / "projects" / f"-home-{user}-projects-{repo_slug}"
        )

    session_file = claude_projects / f"{session}.jsonl"
    sa_src = claude_projects / session / "subagents"

    if "/" in repo or ".." in repo:
        raise ValueError("repo 名称非法")

    if not project_dir.is_dir():
        raise FileNotFoundError(f"项目目录不存在: {project_dir}")
    if not session_file.is_file():
        raise FileNotFoundError(f"session 文件不存在: {session_file}")

    doc_abs_path = (out_root.resolve() / "doc").resolve()
    doc_abs_path.mkdir(parents=True, exist_ok=True)

    build_doc_only = env.get("BUILD_DOC_ONLY", "").strip()

    if not build_doc_only:
        sessions_root = out_root / "sessions"
        session1 = sessions_root / "session1"
        dst_jsonl = session1 / "session.jsonl"
        session1.mkdir(parents=True, exist_ok=True)
        _build_out(f"复制 session → {dst_jsonl}")
        shutil.copy2(session_file, dst_jsonl)

        _build_out("归一化 JSONL 内 model 字段")
        changed = normalize_session_jsonl(dst_jsonl)
        _build_out(f'已统一 JSONL 内 model 字段为 "model"（变更对象数: {changed}）')

        _build_out("复制 subagents")
        if sa_src.is_dir():
            dst_sa = session1 / "subagents"
            dst_sa.mkdir(parents=True, exist_ok=True)
            shutil.copytree(sa_src, dst_sa, dirs_exist_ok=True)
            _build_out(f"subagents复制完成: {sa_src} → {dst_sa}")
        else:
            _build_out(f"未找到 subagents 目录（跳过）: {sa_src}")

    prompt = _build_doc_prompt(doc_abs_path)
    use_mock = env.get("BUILD_USE_MOCK_CLAUDE", "").strip() == "1"

    rr = Path.cwd().resolve()
    if use_mock:
        prev = env.get("PYTHONPATH", "").strip()
        env["PYTHONPATH"] = str(rr) + (os.pathsep + prev if prev else "")

    rc = 1
    for attempt in range(1, _MAX_CLAUDE_ATTEMPTS + 1):
        _build_out(f"claude --resume {session}（第 {attempt}/{_MAX_CLAUDE_ATTEMPTS} 次）")
        if use_mock:
            cmd = [
                sys.executable,
                "-m",
                "cu.testing.mock_claude",
                "-p",
                "--allowedTools",
                "Edit,Write,Read,Bash,MultiEdit",
                "--resume",
                session,
                prompt,
            ]
            proc = subprocess.run(
                cmd,
                cwd=str(project_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            cmd = [
                "claude",
                "-p",
                "--allowedTools",
                "Edit,Write,Read,Bash,MultiEdit",
                "--resume",
                session,
            ]
            proc = subprocess.run(
                cmd,
                cwd=str(project_dir),
                env=env,
                stdin=subprocess.PIPE,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        rc = proc.returncode
        if rc == 0:
            break
        _build_err(f"claude 退出码 {rc}，将重试")

    if rc != 0:
        raise RuntimeError(f"claude 在 {_MAX_CLAUDE_ATTEMPTS} 次尝试后仍失败，最后退出码: {rc}")

    if use_mock:
        _maybe_stub_mock_docs(doc_abs_path)

    _build_out("文档生成完成")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) != 2:
        _build_err("用法: python -m cu.pipeline.build_docs <repo> <session>")
        return 1
    try:
        run_build_docs(argv[0], argv[1])
    except Exception as e:
        _build_err(str(e))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
