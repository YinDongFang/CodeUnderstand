"""代码体量评估：stdout 单行 ``easy`` | ``medium`` | ``difficult``。"""
from __future__ import annotations

import os
import sys

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "node_modules",
        "bower_components",
        "vendor",
        ".venv",
        "venv",
        "__pycache__",
        ".tox",
        ".gradle",
        "build",
        "dist",
        "out",
        "target",
        ".next",
        "coverage",
        "Pods",
    }
)

SPECIAL_FILES = frozenset(
    {
        "CMakeLists.txt",
        "Makefile",
        "makefile",
        "GNUmakefile",
        "Dockerfile",
        "dockerfile",
    }
)

CODE_SUFFIXES = frozenset(
    {
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".cxx",
        ".hpp",
        ".hh",
        ".m",
        ".mm",
        ".swift",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".groovy",
        ".py",
        ".pyw",
        ".pyi",
        ".rb",
        ".php",
        ".pl",
        ".pm",
        ".r",
        ".jl",
        ".lua",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".vue",
        ".svelte",
        ".astro",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".styl",
        ".html",
        ".htm",
        ".svg",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".json",
        ".jsonc",
        ".xml",
        ".xsl",
        ".gradle",
        ".properties",
        ".go",
        ".rs",
        ".zig",
        ".nim",
        ".dart",
        ".ex",
        ".exs",
        ".erl",
        ".hrl",
        ".clj",
        ".cljs",
        ".edn",
        ".fs",
        ".fsx",
        ".cs",
        ".vb",
        ".fsproj",
        ".sql",
        ".graphql",
        ".gql",
        ".proto",
        ".thrift",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".bat",
        ".cmd",
        ".cmake",
        ".jsp",
        ".jspx",
        ".asp",
        ".aspx",
        ".cshtml",
        ".mdx",
        ".rsx",
        ".hbs",
        ".ejs",
        ".pug",
        ".R",
    }
)


CODE_SUFFIX_LOWER = {x.lower() for x in CODE_SUFFIXES}


def _is_code_file(filename: str) -> bool:
    if filename in SPECIAL_FILES:
        return True
    lower_name = filename.lower()
    if lower_name.endswith(".gradle.kts"):
        return True
    if lower_name.endswith(".dockerfile"):
        return True
    suf = os.path.splitext(filename)[1].lower()
    return suf in CODE_SUFFIX_LOWER


def evaluate_repo_difficulty(code_dir: str) -> str:
    """返回 ``easy`` / ``medium`` / ``difficult``。"""
    root = os.path.abspath(code_dir)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"目录不存在: {root}")

    lines_total = 0
    files_count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        for fn in filenames:
            if not _is_code_file(fn):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                with open(fp, "rb") as bf:
                    content = bf.read()
            except OSError:
                continue
            files_count += 1
            lines_total += content.count(b"\n") + (
                1 if content and not content.endswith(b"\n") else 0
            )

    difficulty = "medium"
    if lines_total < 16000 and files_count < 45:
        difficulty = "easy"
    elif lines_total > 160000 and files_count > 450:
        difficulty = "difficult"
    elif 16000 <= lines_total <= 50000 and 45 <= files_count <= 450:
        difficulty = "medium"

    return difficulty


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) < 1:
        print("用法: python -m cu.pipeline.evaluate <target_path>", file=sys.stderr)
        return 1
    try:
        print(evaluate_repo_difficulty(argv[0]))
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
