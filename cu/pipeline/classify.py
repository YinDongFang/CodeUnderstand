"""项目分类：stdout 单行 ``main_type``。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _readme_summary(readme_path: str, max_chars: int = 1000) -> str:
    if not os.path.isfile(readme_path):
        return "（项目中未找到 README.md）"
    raw = Path(readme_path).read_text(encoding="utf-8", errors="replace")
    return raw[:max_chars] if len(raw) > max_chars else raw


def _strip_response_body(raw: str) -> str:
    s = raw.lstrip("\ufeff").strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s, count=1)
    s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _build_query(github_url: str, summary: str) -> str:
    return f"""You are an expert software analyst. Analyze the following code project based on the summary below.

GitHub repository:
{github_url}

Project summary:
{summary}

Classify the project into exactly one category. The value of main_type MUST be one of these English strings (use spelling and punctuation exactly):
  - Platform Application
  - Middleware
  - Framework/Tools
  - AI
  - Game & Multimedia
  - Security
  - Learning/Tutorial
  - Open Source Library / SDK
  - Other
  - Unknown

Return ONLY a single JSON object with exactly one key: main_type (string). No markdown, no code fences, no extra keys or text.
Example JSON: {{"main_type":"Framework/Tools"}}"""


def classify_main_type(
    code_dir: str,
    github_url: str,
    *,
    env: dict[str, str] | None = None,
    repo_root: str | None = None,
) -> str:
    """调用 Claude（或 mock）；失败时返回 ``Unknown``。"""
    code_dir = os.path.abspath(code_dir)
    if not os.path.isdir(code_dir):
        raise FileNotFoundError(f"目录不存在: {code_dir}")

    readme_path = os.path.join(code_dir, "README.md")
    summary = _readme_summary(readme_path)
    query = _build_query(github_url, summary)

    env = dict(env or os.environ)
    use_mock = env.get("CLASSIFY_USE_MOCK_CLAUDE", "").strip() == "1"

    rr = (repo_root or "").strip()
    if use_mock and rr:
        prev = env.get("PYTHONPATH", "").strip()
        env["PYTHONPATH"] = rr + (os.pathsep + prev if prev else "")

    if use_mock:
        cmd = [
            sys.executable,
            "-m",
            "cu.testing.mock_claude",
            "-p",
            "--model",
            "claude-sonnet-4-6",
            "--effort",
            "medium",
            query,
        ]
    else:
        cmd = [
            "claude",
            "-p",
            "--model",
            "claude-sonnet-4-6",
            "--effort",
            "medium",
            query,
        ]

    r = subprocess.run(
        cmd,
        env=env,
        cwd=repo_root or os.getcwd(),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if r.returncode != 0:
        return "Unknown"

    body = _strip_response_body(r.stdout or "")
    try:
        obj = json.loads(body)
        mt = obj.get("main_type", "Unknown")
        return str(mt) if mt else "Unknown"
    except json.JSONDecodeError:
        return "Unknown"


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) < 2:
        print("用法: python -m cu.pipeline.classify <code目录> <github>", file=sys.stderr)
        return 1
    rr = str(Path.cwd())
    print(classify_main_type(argv[0], argv[1], env=os.environ.copy(), repo_root=rr))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
