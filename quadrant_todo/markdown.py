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
- 表格（GFM 风格：`| 表头 |` + 分隔行 `| --- |`，支持 `:---`/`---:`/`:--:` 对齐）
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


# 表格分隔行：每格为 `:?-{1,}:?`，可省略首尾 `|`，但必须至少含一个 `|` 才视为表格分隔行
_TABLE_SEP_RE = re.compile(
    r"^\s*\|?\s*:?\s*-{1,}\s*:?\s*(\|\s*:?\s*-{1,}\s*:?\s*)*\|?\s*$"
)


def _split_row(row: str) -> list[str]:
    r"""按 `|` 切分表格行，容忍首尾管道符与单元格内转义 `\|`。"""
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    parts = re.split(r"(?<!\\)\|", row)
    return [p.strip().replace("\\|", "|") for p in parts]


def _align_from_sep(cell: str) -> str:
    """由分隔行单元格推断对齐方式：left / center / right / ''。"""
    cell = cell.strip()
    left = cell.startswith(":")
    right = cell.endswith(":")
    if left and right:
        return "center"
    if right:
        return "right"
    if left:
        return "left"
    return ""


def _parse_table(lines: list[str], start: int) -> tuple[str, int]:
    """解析一个 GFM 表格（表头 + 分隔行 + 表体），返回 (html, 下一行索引)。"""
    header = _split_row(lines[start])
    sep = _split_row(lines[start + 1])
    aligns = [_align_from_sep(c) for c in sep]

    body: list[list[str]] = []
    j = start + 2
    while j < len(lines):
        s = lines[j].strip()
        if s == "" or "|" not in s:
            break
        body.append(_split_row(lines[j]))
        j += 1

    def _cell_attrs(align: str) -> str:
        return f' align="{align}"' if align else ""

    th_cells = "".join(
        f"<th{_cell_attrs(a)}>{_inline(html.escape(c, quote=False))}</th>"
        for c, a in zip(header, aligns)
    )
    rows_html = ""
    for row in body:
        cells = "".join(
            f"<td{_cell_attrs(aligns[k] if k < len(aligns) else '')}>"
            f"{_inline(html.escape(c, quote=False))}</td>"
            for k, c in enumerate(row)
        )
        rows_html += f"<tr>{cells}</tr>"

    table = (
        '<table border="1" cellspacing="0" cellpadding="4">'
        f"<thead><tr>{th_cells}</tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        "</table>"
    )
    return table, j


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

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 代码块占位符（独占一行）
        cp = _CODE_PLACEHOLDER_RE.fullmatch(stripped)
        if cp:
            flush_para()
            flush_list()
            flush_quote()
            code = html.escape(code_blocks[int(cp.group(1))], quote=False)
            blocks.append(f"<pre><code>{code}</code></pre>")
            i += 1
            continue

        # 表格：当前行含 `|` 且下一行是分隔行（含 `|` 且形如 `---` / `:--:`）
        if (
            i + 1 < n
            and "|" in stripped
            and "|" in lines[i + 1]
            and _TABLE_SEP_RE.match(lines[i + 1].strip())
        ):
            flush_para()
            flush_list()
            flush_quote()
            table_html, i = _parse_table(lines, i)
            blocks.append(table_html)
            continue

        # 分隔线
        if _HR_RE.fullmatch(stripped):
            flush_para()
            flush_list()
            flush_quote()
            blocks.append("<hr>")
            i += 1
            continue

        # 标题
        hm = _HEADING_RE.fullmatch(stripped)
        if hm:
            flush_para()
            flush_list()
            flush_quote()
            level = len(hm.group(1))
            blocks.append(f"<h{level}>{_inline(html.escape(hm.group(2), quote=False))}</h{level}>")
            i += 1
            continue

        # 引用
        if stripped.startswith(">"):
            flush_para()
            flush_list()
            quote.append(stripped[1:].strip())
            i += 1
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
            i += 1
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
            i += 1
            continue

        # 空行：分段
        if stripped == "":
            flush_para()
            flush_list()
            flush_quote()
            i += 1
            continue

        # 普通文本：累积成段落（段内换行视为空格）
        para.append(line)
        i += 1

    flush_para()
    flush_list()
    flush_quote()
    return "\n".join(blocks)
