"""双 Agent 编排：进程 stdout 仅输出 repo 的 ``session_id``。"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from cu.logging_config import configure_logging
from cu.pipeline.loop_render import render_prompt_template
from cu.pipeline_env import resolve_state_root

VIEW_ANGLES = [
    "实现原理",
    "输出原理",
    "逻辑链路",
    "函数细节",
    "变量使用",
    "异常场景",
    "边界条件",
    "模块职责",
    "设计思想",
    "业务流程",
]

_MAX_CLAUDE_ATTEMPTS = 4

_log = logging.getLogger(__name__)


def _loop_out(msg: str) -> None:
    _log.info("%s", msg)


def _loop_err(msg: str) -> None:
    _log.warning("%s", msg)


def _fold_one_line(s: str) -> str:
    return s.replace("\r", "").replace("\n", " ")


def _preview_text(s: str, n: int = 30) -> str:
    one = _fold_one_line(s)
    return one if len(one) <= n else one[:n] + "..."


def _log_compact_line(s: str) -> str:
    max_len = int(os.environ.get("LOOP_LOG_COMPACT_MAX", "200"))
    folded = _fold_one_line(s)
    lim = max(max_len - 3, 1) if max_len > 3 else 1
    if len(folded) > max_len:
        return folded[:lim] + "..."
    return folded


def _str_trim(s: str) -> str:
    return s.strip()


def questions_from_text_to_lines(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if re.match(r"^\s*#", line):
            continue
        if re.match(r"^\s*$", line):
            continue
        out.append(line)
    return out


def resume_scan_jsonl(path: Path) -> tuple[int, str]:
    completed = 0
    last_user = ""
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "user":
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        t = content.strip()
        if not t or t.startswith("<task-notification>"):
            continue
        completed += 1
        last_user = t
    return completed, last_user


def parse_first_json_doc(raw: str) -> dict | None:
    raw = raw.replace("\r", "")
    dec = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            doc, _ = dec.raw_decode(raw, i)
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict):
            return doc
    return None


def _result_from_json_doc(doc: dict) -> str:
    r = doc.get("result")
    if r is None:
        return ""
    if isinstance(r, str):
        return r
    if isinstance(r, (bool, int, float)):
        return str(r)
    return json.dumps(r, ensure_ascii=False)


def _session_id_from_doc(doc: dict) -> str:
    sid = doc.get("session_id", "")
    if sid is None:
        return ""
    return str(sid)


def _rand_between(lo: int, hi: int) -> int:
    return random.randint(lo, hi)


def _pick_views_text(
    angles: list[str],
    views_min: int,
    views_max: int,
) -> str:
    n = len(angles)
    lo = max(1, views_min)
    hi = min(max(lo, views_max), n)
    k = _rand_between(lo, hi)
    idxs = random.sample(range(n), k)
    return "、".join(angles[i] for i in idxs)


def _compute_entry_n(
    phase1_target: int,
    repo_prompt_count: int,
    entry_n_min: int,
    entry_n_max: int,
) -> int:
    rem = phase1_target - repo_prompt_count
    if rem <= 0:
        return entry_n_min
    cycles = (rem + 6) // 7
    if cycles < 1:
        cycles = 1
    n = (rem + cycles - 1) // cycles
    n = max(entry_n_min, min(entry_n_max, n))
    if rem <= 18 and cycles >= 2 and n >= 3:
        n = 2
    return n


def _find_session_jsonl(claude_project_dir: Path) -> Path | None:
    if not claude_project_dir.is_dir():
        return None
    p = claude_project_dir / "session.jsonl"
    if p.is_file():
        return p
    jsonls = sorted(
        claude_project_dir.glob("*.jsonl"),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )
    return jsonls[0] if jsonls else None


def _ensure_mock_pythonpath(env: dict[str, str], repo_root: Path) -> None:
    rr = str(repo_root)
    prev = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = rr + (os.pathsep + prev if prev else "")


def _claude_cmd(
    *,
    cwd: Path,
    agent_dir: Path,
    target_path: Path,
    extra: list[str],
    env: dict[str, str],
) -> list[str]:
    use_mock = env.get("LOOP_USE_MOCK_CLAUDE", "").strip() == "1"
    if use_mock:
        return [sys.executable, "-m", "cu.testing.mock_claude", *extra]
    if cwd.resolve() == agent_dir.resolve():
        model = env.get("LOOP_AGENT_CLAUDE_MODEL", "claude-sonnet-4-6")
        effort = env.get("LOOP_AGENT_CLAUDE_EFFORT", "medium")
        return [
            "claude",
            "--model",
            model,
            "--effort",
            effort,
            "--add-dir",
            str(target_path),
            *extra,
        ]
    return ["claude", *extra]


def _run_claude_capture(
    cwd: Path,
    agent_dir: Path,
    target_path: Path,
    extra: list[str],
    env: dict[str, str],
) -> tuple[int, str]:
    cmd = _claude_cmd(
        cwd=cwd, agent_dir=agent_dir, target_path=target_path, extra=extra, env=env
    )
    use_mock = env.get("LOOP_USE_MOCK_CLAUDE", "").strip() == "1"
    if use_mock:
        _ensure_mock_pythonpath(env, Path.cwd().resolve())

    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    out = proc.stdout or ""
    return proc.returncode, out


def _loop_infer_side(cwd: Path, target_path: Path, agent_dir: Path) -> str:
    if cwd.resolve() == target_path.resolve():
        return "Repo"
    if cwd.resolve() == agent_dir.resolve():
        return "Agent"
    return "Unknown"


def _loop_log_turn(
    env: dict[str, str],
    role: str,
    prompt: str,
    result: str,
    *,
    use_mock: bool,
    target_path: Path,
    agent_dir: Path,
) -> None:
    path = env.get("LOOP_LOG_FILE", "").strip()
    if not path:
        return
    lp = Path(path)
    if use_mock:
        block = (
            "=====================\n"
            f"{role}\n\n"
            "Prompt：\n"
            f"{prompt}\n\n"
            "Result：\n"
            f"{result}\n\n"
        )
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.open("a", encoding="utf-8").write(block)
    else:
        ps = _log_compact_line(prompt)
        rs = _log_compact_line(result)
        block = (
            "=====================\n"
            f"{role}\n"
            f"Prompt：{ps}\n"
            f"Result：{rs}\n\n"
        )
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.open("a", encoding="utf-8").write(block)


def _loop_log_bind_repo_session(
    env: dict[str, str],
    *,
    session_repo: str,
    run_id: str,
    repo: str,
) -> None:
    if not session_repo or not env.get("LOOP_LOG_FILE"):
        return
    cur = Path(env["LOOP_LOG_FILE"])
    base = cur.name
    if base.startswith("repo__"):
        return
    parent = cur.parent
    want = parent / f"repo__{session_repo}__{run_id}.txt"
    if want.exists() and cur.resolve() != want.resolve():
        want = parent / f"repo__{session_repo}__{repo}__{run_id}.txt"
    if cur.resolve() != want.resolve():
        try:
            cur.rename(want)
            env["LOOP_LOG_FILE"] = str(want)
            _loop_out(f"会话日志已关联 repo session: {want}")
        except OSError:
            pass


def claude_json_first(
    cwd: Path,
    prompt: str,
    *,
    agent_dir: Path,
    target_path: Path,
    env: dict[str, str],
) -> tuple[str, str]:
    attempt = 0
    while attempt < _MAX_CLAUDE_ATTEMPTS:
        attempt += 1
        _loop_out(f"  [json-first] 尝试 {attempt}/{_MAX_CLAUDE_ATTEMPTS} cwd={cwd}")
        ex, raw = _run_claude_capture(
            cwd,
            agent_dir,
            target_path,
            ["--output-format", "json", "-p", prompt],
            env,
        )
        use_mock = env.get("LOOP_USE_MOCK_CLAUDE", "").strip() == "1"
        if ex != 0:
            _loop_err(f"  claude 退出码 {ex}，重试")
            continue
        doc = parse_first_json_doc(raw)
        if not doc:
            _loop_err(f"  JSON 解析失败，摘要: {_preview_text(raw)}")
            continue
        sid = _session_id_from_doc(doc)
        if not sid or sid == "null":
            _loop_err("  未解析到 session_id，重试")
            continue
        out = _result_from_json_doc(doc)
        role = _loop_infer_side(cwd, target_path, agent_dir)
        _loop_log_turn(env, role, prompt, out, use_mock=use_mock, target_path=target_path, agent_dir=agent_dir)
        return sid, out
    raise RuntimeError("PromptEntry/json-first：已达最大重试次数")


def claude_resume_text(
    cwd: Path,
    sid: str,
    prompt: str,
    *,
    agent_dir: Path,
    target_path: Path,
    env: dict[str, str],
) -> str:
    attempt = 0
    while attempt < _MAX_CLAUDE_ATTEMPTS:
        attempt += 1
        _loop_out(f"  [resume] 尝试 {attempt}/{_MAX_CLAUDE_ATTEMPTS} cwd={cwd}")
        ex, raw = _run_claude_capture(
            cwd,
            agent_dir,
            target_path,
            ["-r", sid, "-c", "-p", prompt],
            env,
        )
        use_mock = env.get("LOOP_USE_MOCK_CLAUDE", "").strip() == "1"
        if ex != 0:
            _loop_err(f"  claude 退出码 {ex}，重试")
            continue
        role = _loop_infer_side(cwd, target_path, agent_dir)
        _loop_log_turn(env, role, prompt, raw, use_mock=use_mock, target_path=target_path, agent_dir=agent_dir)
        return raw
    raise RuntimeError("claude resume：已达最大重试次数")


def run_loop(target_path_raw: str, repo: str) -> str:
    """执行完整 loop；返回 repo 侧 ``session_id``（须打印到 stdout）。"""
    env = os.environ.copy()
    target_path = Path(target_path_raw).expanduser()
    if not target_path.is_absolute():
        target_path = (Path.cwd() / target_path).resolve()
    else:
        target_path = target_path.resolve()
    if not target_path.is_dir():
        raise FileNotFoundError(f"目录不存在: {target_path}")

    repo_root = Path.cwd().resolve()
    agent_src = repo_root / "agent"
    loop_render_prompt_py = repo_root / "loop_render_prompt.py"
    if not agent_src.is_dir():
        raise FileNotFoundError(f"未找到 {agent_src}")
    for name in ("PromptEntry.md", "PromptDeeper.md", "PromptSummary.md", "PromptFinal.md"):
        if not (agent_src / name).is_file():
            raise FileNotFoundError(f"未找到 {agent_src / name}")
    if not loop_render_prompt_py.is_file():
        raise FileNotFoundError(f"未找到 {loop_render_prompt_py}")

    state_root = resolve_state_root()
    agent_dir = state_root / f"agent-{repo}"

    user = os.environ.get("USER") or ""
    if not user.strip():
        user = "unknown"

    phase1_target = int(env.get("LOOP_PHASE1_TARGET", "37"))
    total_target = int(env.get("LOOP_TOTAL_TARGET", "38"))
    entry_n_min = int(env.get("LOOP_ENTRY_N_MIN", "4"))
    entry_n_max = int(env.get("LOOP_ENTRY_N_MAX", "5"))
    deeper2_prob = int(env.get("LOOP_DEEPER2_PROB", "60"))
    deeper2_max_qs = int(env.get("LOOP_DEEPER2_MAX_QS", "3"))
    views_angle_min = int(env.get("LOOP_VIEWS_ANGLE_MIN", "2"))
    views_angle_max = int(env.get("LOOP_VIEWS_ANGLE_MAX", "4"))

    use_mock = env.get("LOOP_USE_MOCK_CLAUDE", "").strip() == "1"
    if use_mock:
        _loop_out("LOOP_USE_MOCK_CLAUDE=1（会话日志仍写入 LOOP_LOG_FILE）")

    run_id = f"{repo}_{os.getpid()}"
    work = Path(tempfile.mkdtemp(prefix=f"loop_{run_id}_"))
    try:
        if use_mock:
            seq_file = work / ".mock_seq"
            seq_file.write_text("0\n", encoding="utf-8")
            env["LOOP_MOCK_SEQ_FILE"] = str(seq_file)

        repo_slug = repo.replace("_", "-")
        claude_project_dir = Path.home() / ".claude" / "projects" / f"-home-{user}-projects-{repo_slug}"

        session_jsonl_path = _find_session_jsonl(claude_project_dir)
        session_repo = ""
        repo_prompt_count = 0
        if session_jsonl_path is not None:
            session_repo = session_jsonl_path.stem
            repo_prompt_count, _ = resume_scan_jsonl(session_jsonl_path)
            _loop_out(
                f"恢复 repo 会话: {session_jsonl_path} session_id={session_repo} "
                f"已完成用户锚点={repo_prompt_count}"
            )
        else:
            _loop_out("未检测到 repo 侧历史 jsonl，从零开始")

        if use_mock and not env.get("LOOP_MOCK_USE_JSONL_RESUME", "").strip():
            session_jsonl_path = None
            session_repo = ""
            repo_prompt_count = 0
            _loop_out("Mock：已忽略 jsonl 恢复（LOOP_MOCK_USE_JSONL_RESUME=1 可保留）")

        loop_log_dir = Path(env.get("LOOP_LOG_DIR", state_root / "loop_logs"))
        loop_log_dir.mkdir(parents=True, exist_ok=True)
        if env.get("LOOP_LOG_FILE"):
            loop_log_file = Path(env["LOOP_LOG_FILE"])
        elif session_repo:
            loop_log_file = loop_log_dir / f"repo__{session_repo}__{run_id}.txt"
        else:
            loop_log_file = loop_log_dir / f"run__{repo}__{run_id}.txt"
        loop_log_file.write_text("", encoding="utf-8")
        env["LOOP_LOG_FILE"] = str(loop_log_file)
        _loop_out(f"会话日志: {loop_log_file}")

        session_agent = ""
        transcript_all = ""

        if repo_prompt_count >= total_target:
            _loop_out(f"已达 TOTAL_TARGET={total_target}，无需运行")
            return session_repo

        if repo_prompt_count >= phase1_target:
            _loop_out("已超过阶段一目标，直接进入 PromptFinal 补齐阶段")

        if not env.get("LOOP_SKIP_AGENT_RESET", "").strip():
            _loop_out(f"复制模板 {agent_src} -> {agent_dir}")
            shutil.rmtree(agent_dir, ignore_errors=True)
            shutil.copytree(agent_src, agent_dir)
        else:
            _loop_out("LOOP_SKIP_AGENT_RESET 已设置，跳过删除/复制 agent")
            if not agent_dir.is_dir():
                shutil.copytree(agent_src, agent_dir)

        while repo_prompt_count < phase1_target:
            entry_n = _compute_entry_n(
                phase1_target, repo_prompt_count, entry_n_min, entry_n_max
            )
            prompt_entry_path = agent_dir / "PromptEntry.md"
            tpl = prompt_entry_path.read_text(encoding="utf-8")
            prompt_entry = tpl.replace("{url}", str(target_path)).replace("{n}", str(entry_n))

            if not session_agent:
                sid, entry_text = claude_json_first(
                    agent_dir,
                    prompt_entry,
                    agent_dir=agent_dir,
                    target_path=target_path,
                    env=env,
                )
                session_agent = sid
                _loop_err(f"session_agent(uuid)={session_agent}")
            else:
                entry_text = claude_resume_text(
                    agent_dir,
                    session_agent,
                    prompt_entry,
                    agent_dir=agent_dir,
                    target_path=target_path,
                    env=env,
                )

            all_entry = questions_from_text_to_lines(entry_text)
            if not all_entry:
                raise RuntimeError("PromptEntry 未解析到有效题目行")
            entry_questions = all_entry[: min(entry_n, len(all_entry))]
            if not entry_questions:
                raise RuntimeError("截取后无有效题目")
            _loop_out(f"本轮入口题数: {len(entry_questions)}")
            dive_rounds = len(entry_questions)
            _loop_out(
                f"========== 大循环: repo_count={repo_prompt_count}/{phase1_target} "
                f"entry_n={entry_n} dive_rounds={dive_rounds}（=入口题条数）=========="
            )

            for dr in range(dive_rounds):
                if repo_prompt_count >= phase1_target:
                    _loop_out("已达阶段一目标，提前结束本轮 dive")
                    break
                q = entry_questions[dr]
                _loop_out(f"---------- dive {dr + 1}/{dive_rounds}（入口题 {dr + 1}）----------")
                _loop_out(f"3.2.1 repo Q: {_preview_text(q)}")

                if not session_repo:
                    sid_repo, a_repo = claude_json_first(
                        target_path,
                        q,
                        agent_dir=agent_dir,
                        target_path=target_path,
                        env=env,
                    )
                    session_repo = sid_repo
                    _loop_log_bind_repo_session(
                        env,
                        session_repo=session_repo,
                        run_id=run_id,
                        repo=repo,
                    )
                    _loop_err(f"session_repo(uuid)={session_repo}")
                else:
                    a_repo = claude_resume_text(
                        target_path,
                        session_repo,
                        q,
                        agent_dir=agent_dir,
                        target_path=target_path,
                        env=env,
                    )
                repo_prompt_count += 1

                transcript_dive = f"Q1:\n{q}\nA1:\n{a_repo}\n"

                _loop_out(f"3.2.2 agent-{repo} deeper")
                q_file = work / "q.txt"
                a1_file = work / "a1.txt"
                views_file = work / "views.txt"
                q_file.write_text(q, encoding="utf-8")
                a1_file.write_text(a_repo, encoding="utf-8")
                views_file.write_text(
                    _pick_views_text(VIEW_ANGLES, views_angle_min, views_angle_max),
                    encoding="utf-8",
                )
                deeper_out = work / "deeper.md"
                render_prompt_template(
                    agent_dir / "PromptDeeper.md",
                    deeper_out,
                    {"question": q_file, "anwser": a1_file, "views": views_file},
                )
                prompt_deeper = deeper_out.read_text(encoding="utf-8")
                deeper_pack = claude_resume_text(
                    agent_dir,
                    session_agent,
                    prompt_deeper,
                    agent_dir=agent_dir,
                    target_path=target_path,
                    env=env,
                )
                deeper_qs = questions_from_text_to_lines(deeper_pack)
                _loop_out(f"3.2.2 新题数: {len(deeper_qs)}")

                qi = 0
                for dq in deeper_qs:
                    qi += 1
                    if repo_prompt_count >= phase1_target:
                        _loop_out("已达阶段一目标，跳过剩余 deeper 子题")
                        break
                    _loop_out(f"3.2.3 repo 子题 {qi}: {_preview_text(dq)}")
                    ad = claude_resume_text(
                        target_path,
                        session_repo,
                        dq,
                        agent_dir=agent_dir,
                        target_path=target_path,
                        env=env,
                    )
                    repo_prompt_count += 1
                    transcript_dive += f"Q_deeper_{qi}:\n{dq}\nA_deeper_{qi}:\n{ad}\n"

                    if deeper2_prob > 0 and repo_prompt_count < phase1_target:
                        if random.randint(0, 99) < deeper2_prob:
                            _loop_out(
                                f"3.2.3b 二层 deeper（概率 {deeper2_prob}% 已命中，最多 {deeper2_max_qs} 条子题）"
                            )
                            q2 = work / "q2.txt"
                            a2 = work / "a2.txt"
                            v2 = work / "views2.txt"
                            q2.write_text(dq, encoding="utf-8")
                            a2.write_text(ad, encoding="utf-8")
                            v2.write_text(
                                _pick_views_text(VIEW_ANGLES, views_angle_min, views_angle_max),
                                encoding="utf-8",
                            )
                            deeper2_out = work / "deeper2.md"
                            render_prompt_template(
                                agent_dir / "PromptDeeper.md",
                                deeper2_out,
                                {"question": q2, "anwser": a2, "views": v2},
                            )
                            prompt_deeper2 = deeper2_out.read_text(encoding="utf-8")
                            deeper2_pack = claude_resume_text(
                                agent_dir,
                                session_agent,
                                prompt_deeper2,
                                agent_dir=agent_dir,
                                target_path=target_path,
                                env=env,
                            )
                            deeper2_qs = questions_from_text_to_lines(deeper2_pack)
                            _loop_out(
                                f"3.2.3b 二层新题数: {len(deeper2_qs)}（将截断至 {deeper2_max_qs}）"
                            )
                            d2i = 0
                            for d2q in deeper2_qs:
                                d2i += 1
                                if d2i > deeper2_max_qs:
                                    break
                                if repo_prompt_count >= phase1_target:
                                    _loop_out("已达阶段一目标，跳过剩余二层子题")
                                    break
                                _loop_out(f"3.2.3b repo 二层子题 {qi}.{d2i}: {_preview_text(d2q)}")
                                a2ans = claude_resume_text(
                                    target_path,
                                    session_repo,
                                    d2q,
                                    agent_dir=agent_dir,
                                    target_path=target_path,
                                    env=env,
                                )
                                repo_prompt_count += 1
                                transcript_dive += (
                                    f"Q_deeper2_{qi}_{d2i}:\n{d2q}\n"
                                    f"A_deeper2_{qi}_{d2i}:\n{a2ans}\n"
                                )

                if repo_prompt_count >= phase1_target:
                    _loop_out("已达阶段一目标，跳过 Summary")
                else:
                    _loop_out("3.2.4 PromptSummary -> agent 生成 2 条汇总题 -> repo 逐条作答")
                    td = work / "transcript_dive.txt"
                    vs = work / "views_summary.txt"
                    td.write_text(transcript_dive, encoding="utf-8")
                    vs.write_text(
                        _pick_views_text(VIEW_ANGLES, views_angle_min, views_angle_max),
                        encoding="utf-8",
                    )
                    summary_out = work / "summary.md"
                    render_prompt_template(
                        agent_dir / "PromptSummary.md",
                        summary_out,
                        {
                            "question": q_file,
                            "anwser": a1_file,
                            "views": vs,
                            "transcript": td,
                        },
                    )
                    sum_prompt = summary_out.read_text(encoding="utf-8")
                    sum_agent_out = claude_resume_text(
                        agent_dir,
                        session_agent,
                        sum_prompt,
                        agent_dir=agent_dir,
                        target_path=target_path,
                        env=env,
                    )
                    sum_qs = questions_from_text_to_lines(sum_agent_out)
                    transcript_dive += (
                        f"\nQ_summary_agent_prompt:\n{sum_prompt}\n"
                        f"A_summary_agent_gen:\n{sum_agent_out}\n"
                    )
                    if not sum_qs:
                        sq = _str_trim(sum_agent_out)
                        if repo_prompt_count < phase1_target:
                            _loop_out(f"3.2.4 repo 汇总题（单行回退）: {_preview_text(sq)}")
                            asum = claude_resume_text(
                                target_path,
                                session_repo,
                                sq,
                                agent_dir=agent_dir,
                                target_path=target_path,
                                env=env,
                            )
                            repo_prompt_count += 1
                            transcript_dive += f"Q_summary_repo_1:\n{sq}\nA_summary_1:\n{asum}\n"
                    else:
                        si = 0
                        for sq in sum_qs:
                            si += 1
                            if repo_prompt_count >= phase1_target:
                                _loop_out("已达阶段一目标，跳过剩余 summary 子题")
                                break
                            _loop_out(f"3.2.4 repo 汇总子题 {si}: {_preview_text(sq)}")
                            asum = claude_resume_text(
                                target_path,
                                session_repo,
                                sq,
                                agent_dir=agent_dir,
                                target_path=target_path,
                                env=env,
                            )
                            repo_prompt_count += 1
                            transcript_dive += (
                                f"Q_summary_repo_{si}:\n{sq}\nA_summary_{si}:\n{asum}\n"
                            )

                transcript_all += transcript_dive + "\n"

        if not session_repo:
            raise RuntimeError("错误: repo session 为空")

        _loop_out(
            f"========== 阶段二 PromptFinal 补齐至 {total_target}（当前 {repo_prompt_count}）=========="
        )
        while repo_prompt_count < total_target:
            ta = work / "transcript_all.txt"
            ta.write_text(transcript_all, encoding="utf-8")
            fo = work / "final.md"
            render_prompt_template(
                agent_dir / "PromptFinal.md",
                fo,
                {"transcript": ta},
            )
            final_ask = fo.read_text(encoding="utf-8")
            final_q = claude_resume_text(
                agent_dir,
                session_agent,
                final_ask,
                agent_dir=agent_dir,
                target_path=target_path,
                env=env,
            )
            fql = questions_from_text_to_lines(final_q)
            if fql:
                final_q_one = fql[0]
            else:
                final_q_one = _str_trim(final_q)
            _loop_out(f"补齐题: {_preview_text(final_q_one)}")
            fa = claude_resume_text(
                target_path,
                session_repo,
                final_q_one,
                agent_dir=agent_dir,
                target_path=target_path,
                env=env,
            )
            repo_prompt_count += 1
            transcript_all += f"\nQ_final_fill:\n{final_q_one}\nA_final_fill:\n{fa}\n"
            _loop_out(f"补齐后 repo_count={repo_prompt_count}/{total_target}")

        _loop_out(f"repo 侧累计用户题次数: {repo_prompt_count}")
        _loop_out(f"Session ID (repo): {session_repo}")
        _loop_out("======================================================")
        _loop_out("=                    Loop Done                       =")
        _loop_out("======================================================")
        return session_repo
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) != 2:
        _loop_err("用法: python -m cu.pipeline.loop <target_path> <repo>")
        return 1
    try:
        sid = run_loop(argv[0], argv[1])
    except Exception as e:
        _loop_err(str(e))
        return 1
    print(sid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
