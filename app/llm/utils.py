"""
LLM 通用工具函数（V5.0 新增）
"""
import json
from typing import Optional


def _repair_unescaped_newlines(text: str) -> str:
    """修复字符串值中未转义的换行符（LLM 常见问题）。

    例如 body 字段内的真实换行会使 JSON 非法，这里将字符串内的
    \n 替换为 \\n，同时跳过已被反斜杠转义的字符与字符串外的换行。
    """
    result = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                result.append(ch)
                escaped = False
            elif ch == "\\":
                result.append(ch)
                escaped = True
            elif ch == '"':
                result.append(ch)
                in_string = False
            elif ch in ("\n", "\r"):
                result.append("\\n")
            else:
                result.append(ch)
        else:
            if ch == '"':
                in_string = True
            result.append(ch)
    return "".join(result)


def extract_json(content: str) -> Optional[object]:
    """从 LLM 返回内容中提取 JSON 对象/数组。

    处理场景：
    1. Markdown 代码块包裹（```json ... ```）
    2. 前后夹杂说明文字
    3. 同时支持 JSON 对象 {} 与 JSON 数组 []
    4. 字符串值内未转义的换行符（自动修复后重试）
    """
    if not content:
        return None

    text = content.strip()

    # 移除 Markdown 代码块标记
    if text.startswith("```"):
        lines = text.split("\n")
        in_block = False
        json_lines = []
        for line in lines:
            if line.strip().startswith("```"):
                in_block = not in_block
                continue
            if in_block:
                json_lines.append(line)
        if json_lines:
            text = "\n".join(json_lines).strip()

    if not text:
        return None

    def _try_parse(t):
        try:
            return json.loads(t)
        except (json.JSONDecodeError, TypeError):
            return None

    parsed = _try_parse(text)
    if parsed is not None:
        return parsed

    # 修复未转义换行符后重试（LLM 输出 body 等多行字符串时常见）
    repaired = _repair_unescaped_newlines(text)
    parsed = _try_parse(repaired)
    if parsed is not None:
        return parsed

    # 尝试截取最外层 {} 或 [] 之间的内容
    for start_ch, end_ch in (("{", "}"), ("[", "]")):
        try:
            start = text.index(start_ch)
            end = text.rindex(end_ch) + 1
            candidate = text[start:end]
            parsed = _try_parse(candidate)
            if parsed is not None:
                return parsed
            parsed = _try_parse(_repair_unescaped_newlines(candidate))
            if parsed is not None:
                return parsed
        except (ValueError, json.JSONDecodeError):
            continue

    return None
