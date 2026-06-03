"""Ask Rufus 数据解析：兼容旧版（纯字符串）与新版（questionN → {问题: 问题+解答}）。"""
from __future__ import annotations

import re
from typing import Any


def _question_sort_key(key: str) -> tuple[int, str]:
    m = re.search(r"(\d+)", key or "")
    return (int(m.group(1)) if m else 999, key or "")


def _split_answer(full_text: str, question: str) -> str:
    """从「问题\\n解答」整段文本中拆出解答部分。"""
    full = (full_text or "").strip()
    q = (question or "").strip()
    if not full:
        return ""
    if q and full.lower().startswith(q.lower()):
        rest = full[len(q) :].lstrip("\n").strip()
        return rest if rest else full
    if "\n" in full:
        head, tail = full.split("\n", 1)
        if q and head.strip().lower() == q.lower():
            return tail.strip()
        return tail.strip() if tail.strip() else full
    return full


def extract_ask_rufus_qa(value: Any) -> tuple[str, str]:
    """单条 questionN 的值 → (问题, 解答)。"""
    if isinstance(value, dict):
        for q_text, full_text in value.items():
            question = str(q_text or "").strip()
            full = str(full_text or "").strip()
            if not question and full:
                if "\n" in full:
                    parts = full.split("\n", 1)
                    return parts[0].strip(), parts[1].strip()
                return full, ""
            answer = _split_answer(full, question)
            return question, answer
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "", ""
        if "\n" in text:
            parts = text.split("\n", 1)
            return parts[0].strip(), parts[1].strip()
        return text, ""
    return "", ""


def parse_ask_rufus_items(data: Any) -> list[dict[str, str]]:
    """将 ask_rufus JSON 转为 [{key, question, answer}, ...]。"""
    if not isinstance(data, dict):
        return []
    items: list[dict[str, str]] = []
    for key in sorted(data.keys(), key=_question_sort_key):
        question, answer = extract_ask_rufus_qa(data[key])
        if not question and not answer:
            continue
        items.append(
            {
                "key": str(key),
                "question": question,
                "answer": answer,
            }
        )
    return items


def format_ask_rufus_for_gpt(data: Any) -> str:
    """格式化为 GPT 可读文本，明确「问题 / 解答」对应关系。"""
    items = parse_ask_rufus_items(data)
    if not items:
        return "（暂无 Ask Rufus 数据）"
    lines = [
        "【Ask Rufus 买家问答】",
        "说明：以下每条包含「问题」（买家在 Rufus 场景下的关切）与「解答」（亚马逊/Rufus 给出的参考回答）。",
        "分析时请区分二者：问题反映购买决策关切；解答中的事实、卖点与局限可用于 VOC 与差异化推理。",
        "",
    ]
    for i, item in enumerate(items, 1):
        lines.append(f"--- 条目 {i}（{item['key']}）---")
        lines.append(f"问题：{item['question']}")
        if item["answer"]:
            lines.append(f"解答：{item['answer']}")
        else:
            lines.append("解答：（无单独解答，或旧版仅含问题文本）")
        lines.append("")
    return "\n".join(lines).strip()
