"""生成 ``metadata.json`` / ``questions.json``（难度 ``cu.pipeline.evaluate``，分类 ``cu.pipeline.classify``）。"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from cu.pipeline.classify import classify_main_type
from cu.pipeline.evaluate import evaluate_repo_difficulty
from cu.pipeline_env import normalized_gh_archive_proxy_prefix


def fetch_github_main_language(github_url: str) -> str:
    api_url = github_url.replace("github.com", "api.github.com/repos", 1)
    proxied = normalized_gh_archive_proxy_prefix() + api_url
    try:
        req = urllib.request.Request(
            proxied,
            headers={
                "User-Agent": "code-understand-pipeline",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            blob = resp.read()
        data = json.loads(blob.decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError):
        return "unknown"
    lang = data.get("language") or ""
    if not lang:
        return "unknown"
    return str(lang).lower()


def run_metadata(
    *,
    artifact_root: str,
    github_url: str,
    repo: str,
    repo_layout_root: str,
    env: dict[str, str],
) -> None:
    """写入 ``{artifact_root}/metadata.json`` 与 ``questions.json``。"""
    code_d = os.path.join(artifact_root, "code", repo)
    if not os.path.isdir(code_d):
        raise RuntimeError(f"[compile/metadata] code 目录不存在: {code_d}")

    main_language = fetch_github_main_language(github_url)

    try:
        difficulty_level = evaluate_repo_difficulty(code_d)
    except Exception as e:
        raise RuntimeError(f"[compile/metadata] evaluate: {e}") from e

    try:
        main_type = classify_main_type(
            code_d, github_url, env=env, repo_root=repo_layout_root,
        )
    except Exception as e:
        raise RuntimeError(f"[compile/metadata] classify: {e}") from e

    meta = {
        "basic_info": {
            "repo_name": github_url,
            "main_language": main_language,
            "github": {"url": github_url, "star": 0},
        },
        "category_info": {"main_type": main_type},
        "difficulty": {"level": difficulty_level},
        "extra_info": {"input_token": 0, "output_token": 0},
    }

    meta_path = os.path.join(artifact_root, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")

    qs_path = os.path.join(artifact_root, "questions.json")
    with open(qs_path, "w", encoding="utf-8") as f:
        f.write("[]\n")


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) != 2:
        print(
            "用法: python -m cu.pipeline.metadata <github_url> <repo>",
            file=sys.stderr,
        )
        print("环境变量: ARTIFACT_ROOT（必须）", file=sys.stderr)
        return 1
    art = os.environ.get("ARTIFACT_ROOT", "").strip()
    if not art:
        print("错误: 需要设置环境变量 ARTIFACT_ROOT", file=sys.stderr)
        return 1
    try:
        run_metadata(
            artifact_root=os.path.abspath(art),
            github_url=argv[0],
            repo=argv[1],
            repo_layout_root=os.getcwd(),
            env=os.environ.copy(),
        )
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    print("metadata.json 与 questions.json 已生成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
