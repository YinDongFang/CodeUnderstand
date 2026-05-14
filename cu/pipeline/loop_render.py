"""渲染 loop Prompt 模板（对齐 ``loop_render_prompt.py`` CLI）。"""
from __future__ import annotations

from pathlib import Path


def render_prompt_template(
    template_path: Path | str,
    out_path: Path | str,
    replacements: dict[str, Path | str],
) -> None:
    """将 ``template`` 中 ``{key}`` 替换为对应路径文件内容或字符串。"""
    tpl = Path(template_path).read_text(encoding="utf-8")
    for key, val in replacements.items():
        if isinstance(val, Path):
            content = val.read_text(encoding="utf-8")
        else:
            content = str(val)
        tpl = tpl.replace("{" + key + "}", content)
    Path(out_path).write_text(tpl, encoding="utf-8")
