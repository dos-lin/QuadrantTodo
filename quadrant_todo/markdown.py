"""最小化 Markdown -> HTML 转换器（仅标准库，无第三方依赖）。

支持语法：
- 标题 `#` ~ `######`
- 粗体 `**x**` / `__x__`
- 斜体 `*x*` / `_x_`
- 行内代码 `` `x` ``
- 围栏代码块 ``` ```lang ... ``` ```
- 无序列表 `-` / `*` / `+`
- 有序列表 `1. `
- 引用 `> `
- 分隔线 `---` / `***` / `___`
- 链接 `[text](url)`
- 图片 `![alt](url)` —— **不渲染**（按需求忽略，直接丢弃）

输出供 Qt `QTextEdit.setHtml()` 显示。全程先做 HTML 转义，避免注入与格式错乱。
块级结构用逐行状态机处理；行内标记用受保护的占位符避免互相干扰。
"""

from __future__ import annotations

import html
import re

# 分隔线：三个及以上 * - _（可含空格）
_HR_RE = re.compile(r"^([*]\s*){3,}$|^([-]\s*){3,}$|^([_]\s*){3,}$")
# 标题：1~6 个 #
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
# 无序列表
_UL_RE = re.compile(r"^[-*+]\s+(.*)$")
# 有序列表
_OL_RE = re.compile(r"^\d+\.\s+(.*)$")
# 行内：链接 / 图片
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# 行内：代码 / 粗体 / 斜体
_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(r"\*(.+?)\*|_([^_]+?)_")
# 围栏代码块（含可选语言标识）
_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
# 代码块占位符（整行）
_CODE_PLACEHOLDER_RE = re.compile(r"^\x00CODE(\d+)\x00$")


def _inline(text: str) -> str:
    """对单行/单段纯文本做行内 Markdown 转换（输入已转义）。"""
    # 图片直接丢弃
    text = _IMG_RE.sub("", text)
    # 提取链接，用占位符保护，避免 href 内的 _ / * 被行内规则破坏
    links: list[tuple[str, str]] = []

    def _store_link(m: re.Match) -> str:
        links.append((m.group(1), m.group(2)))
        return f"\x00LINK{len(links) - 1}\x00"

    text = _LINK_RE.sub(_store_link, text)
    text = _CODE_RE.sub(r"<code>\1</code>", text)
    text = _BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _ITALIC_RE.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)

    def _restore_link(m: re.Match) -> str:
        label, url = links[int(m.group(1))]
        return f'<a href="{url}">{label}</a>'

    return re.sub(r"\x00LINK(\d+)\x00", _restore_link, text)


def markdown_to_html(md: str) -> str:
    """把 Markdown 源码转成 Qt 可渲染的 HTML 片段。空输入返回空串。"""
    if not md:
        return ""

    # 1) 先抽出围栏代码块，避免块内内容被块级/行内规则误处理
    code_blocks: list[str] = []

    def _store_code(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\n\x00CODE{len(code_blocks) - 1}\x00\n"

    md = _FENCE_RE.sub(_store_code, md)

    lines = md.split("\n")
    blocks: list[str] = []
    para: list[str] = []
    list_type: str | None = None
    list_items: list[str] = []
    quote: list[str] = []

    def flush_para() -> None:
        if para:
            joined = " ".join(p.strip() for p in para).strip()
            if joined:
                blocks.append("<p>" + _inline(html.escape(joined, quote=False)) + "</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal list_type, list_items
        if list_items:
            tag = list_type or "ul"
            items = "".join(f"<li>{_inline(html.escape(it, quote=False))}</li>" for it in list_items)
            blocks.append(f"<{tag}>{items}</{tag}>")
            list_items = []
            list_type = None

    def flush_quote() -> None:
        nonlocal quote
        if quote:
            inner = "<br>".join(_inline(html.escape(q, quote=False)) for q in quote)
            blocks.append(f"<blockquote>{inner}</blockquote>")
            quote = []

    for line in lines:
        stripped = line.strip()

        # 代码块占位符（独占一行）
        cp = _CODE_PLACEHOLDER_RE.fullmatch(stripped)
        if cp:
            flush_para()
            flush_list()
            flush_quote()
            code = html.escape(code_blocks[int(cp.group(1))], quote=False)
            blocks.append(f"<pre><code>{code}</code></pre>")
            continue

        # 分隔线
        if _HR_RE.fullmatch(stripped):
            flush_para()
            flush_list()
            flush_quote()
            blocks.append("<hr>")
            continue

        # 标题
        hm = _HEADING_RE.fullmatch(stripped)
        if hm:
            flush_para()
            flush_list()
            flush_quote()
            level = len(hm.group(1))
            blocks.append(f"<h{level}>{_inline(html.escape(hm.group(2), quote=False))}</h{level}>")
            continue

        # 引用
        if stripped.startswith(">"):
            flush_para()
            flush_list()
            quote.append(stripped[1:].strip())
            continue

        # 无序列表
        um = _UL_RE.fullmatch(stripped)
        if um:
            flush_para()
            flush_quote()
            if list_type != "ul":
                flush_list()
                list_type = "ul"
            list_items.append(um.group(1))
            continue

        # 有序列表
        om = _OL_RE.fullmatch(stripped)
        if om:
            flush_para()
            flush_quote()
            if list_type != "ol":
                flush_list()
                list_type = "ol"
            list_items.append(om.group(1))
            continue

        # 空行：分段
        if stripped == "":
            flush_para()
            flush_list()
            flush_quote()
            continue

        # 普通文本：累积成段落（段内换行视为空格）
        para.append(line)

    flush_para()
    flush_list()
    flush_quote()
    return "\n".join(blocks)
