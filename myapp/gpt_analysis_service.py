"""
GPT 分析：仅基于对标 ASIN 的 VOC 与 ASIN 集群各竞品 VOC；结果写入 AsinAnalysis / AnalysisDetail（三块）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings

from .ask_rufus_utils import format_ask_rufus_for_gpt
from .models import AiListingGenerationHistory, AnalysisDetail, AsinAnalysis, OriginalAsinData

SYSTEM_PROMPT = (
    "你是一位资深亚马逊产品分析师与消费者洞察专家。\n\n"
    "你将收到三类数据：\n"
    "1）对标 ASIN 的 VOC（消费者评论洞察 JSON 或文本结构）\n"
    "2）ASIN 集群中各竞品 ASIN 的 VOC（可能部分为空）\n"
    "3）Ask Rufus 买家问答：每条含「问题」（买家关切）与「解答」（Rufus/亚马逊参考回答），请区分二者并纳入分析\n\n"
    "你需要严格依据输入中的 VOC 信息推理，不得编造 VOC 中不存在的百分比或事实，若voc数据不全，你可以自行获取正确的信息\n"
    "若某竞品 VOC 为空，请明确说明「数据缺失」，不要虚构其评论内容。\n\n"
    "请输出【单个 JSON 对象】（不要 Markdown 包裹），顶层必须且只能包含以下三个键（键名一字不差）：\n"
    ' "对标ASIN分析"\n'
    ' "ASIN集群分析"\n'
    ' "差异化分析"\n\n'
    "一、「对标ASIN分析」的值必须为对象（object），且必须包含以下 7 个键（键名一字不差），每个值为字符串：\n"
    " 消费者画像的分析主要侧重于目标用户的特征、偏好和行为习惯；"
    " - 使用场景的分析需要明确产品被使用的具体环境和方式、\n"
    " - 未被满足的需求要具体指出哪些需求没有被现有产品满足 \n"
    " - 好评和差评的分析要总结出主要的正面和负面反馈 \n"
    " - 购买动机首先要分析对标asin的所有购买动机，再要深入挖掘驱动消费者购买的核心因素 \n"
    " - 建议和总结则需要基于以上分析提出切实可行的改进建议 \n"
    "并总结出对标 ASIN 的核心竞争力与不足之处，以及差异化方向 \n"
    " 内容需条理清晰，可直接用于业务决策。\n\n"
    "二、「ASIN集群分析」的值必须为对象。侧重「建议与总结」，至少用自然语言覆盖：\n"
    " - 主要对标的哪些人群，哪些人群可能未被对标 ASIN 覆盖或覆盖不足\n"
    " - 主要覆盖哪些场景，哪些使用场景未覆盖或覆盖不足\n"
    " - 哪些需求未被满足\n"
    " - 差评的主要原因及可落地的改进/沟通办法\n"
    " - 购买动机首先要分析asin集群的所有购买动机，再从维度上分析是否仍有未被覆盖的缺口\n"
    " 你可使用附加键（如 建议与总结、人群缺口、场景缺口 等）组织内容，但必须包含键「建议和总结」（字符串），作为本段总述。\n\n"
    "三、「差异化分析」的值必须为对象，必须包含以下键（每个键值为字符串）：\n"
    "  「人群差异化」、「场景差异化」、「需求差异化」、「购买动机差异化」、「VOC定位」、「差评改进方向」。\n"
    "  其中：\n"
    "  - 人群差异化：联系上下文和对标ASIN与集群在目标人群上的差异\n"
    "  - 场景差异化：联系上下文和使用场景覆盖的差异\n"
    "  - 需求差异化：满足的需求与未满足需求的差异\n"
    "  - 购买动机差异化：联系上下文和驱动购买的因素差异\n"
    "  - VOC定位：联系上下文并综合分析对标ASIN和ASIN集群后的VOC核心定位,产出结构必须要按照消费者画像，使用场景，未被满足的需求，购买动机\n"
    "  - 差评改进方向：针对差评提出的具体改进建议\n"
    "  必须附加「结论与建议」键（字符串）作为总结。\n\n"
    "输出必须是合法 JSON：双引号、无注释、无尾逗号。"
)


def _openai_settings() -> Tuple[str, str, str]:
    key = getattr(settings, "OPENAI_API_KEY", "") or ""
    base = getattr(settings, "OPENAI_BASE_URL", "https://api.openai-proxy.org/v1")
    model = getattr(settings, "OPENAI_MODEL", "gpt-5.1")
    return key, base, model


def validate_original_complete(orig: OriginalAsinData) -> Tuple[bool, str]:
    """仅要求对标行有可用 VOC；集群 VOC 可为空（模型会标注数据缺失）。"""
    voc = orig.voc
    has_voc = False
    if voc is None:
        has_voc = False
    elif isinstance(voc, dict):
        has_voc = len(voc) > 0
    elif isinstance(voc, list):
        has_voc = len(voc) > 0
    else:
        has_voc = bool(str(voc).strip())
    if not has_voc:
        return False, f"ASIN {orig.asin} 缺少对标 VOC 数据，无法计算。"
    return True, ""


def _normalize_asin_cluster_field(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        items: list[Any] = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for x in items:
        if not isinstance(x, str):
            continue
        a = x.strip().upper()
        if len(a) == 10 and a.isalnum():
            if a not in seen:
                seen.add(a)
                out.append(a)
    return out


def _voc_nonempty_for_prompt(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, dict):
        return len(v) > 0
    if isinstance(v, list):
        return len(v) > 0
    return bool(v)


def _voc_cluster_map_for_prompt(orig: OriginalAsinData) -> Dict[str, Any]:
    raw = getattr(orig, "voc_cluster", None) or {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        ku = k.strip().upper()
        if len(ku) == 10 and ku.isalnum():
            out[ku] = v
    return out


def _voc_payload_for_prompt(orig: OriginalAsinData) -> Any:
    """对标 VOC + 集群各 ASIN VOC：优先 voc_cluster，否则读库中该 ASIN 行的 voc。"""
    cluster_asins = _normalize_asin_cluster_field(getattr(orig, "asin_cluster", None))
    row_u = (orig.asin or "").strip().upper()
    cluster_asins = [a for a in cluster_asins if a != row_u]
    if not cluster_asins:
        return {
            "对标ASIN": orig.asin,
            "对标ASIN_VOC": orig.voc,
            "ASIN集群": [],
            "ASIN集群各ASIN的VOC": {},
        }
    vc_map = _voc_cluster_map_for_prompt(orig)
    cluster_vocs: dict[str, Any] = {}
    for ca in cluster_asins:
        emb = vc_map.get(ca)
        if _voc_nonempty_for_prompt(emb):
            cluster_vocs[ca] = emb
            continue
        o2 = OriginalAsinData.objects.only("voc", "asin").filter(asin__iexact=ca).first()
        if o2 and _voc_nonempty_for_prompt(o2.voc):
            cluster_vocs[o2.asin] = o2.voc
        else:
            cluster_vocs[ca] = None
    return {
        "对标ASIN": orig.asin,
        "对标ASIN_VOC": orig.voc,
        "ASIN集群": cluster_asins,
        "ASIN集群各ASIN的VOC": cluster_vocs,
    }


def build_user_prompt(orig: OriginalAsinData) -> str:
    asin = orig.asin
    voc_data = _voc_payload_for_prompt(orig)
    asin_link = f"https://www.amazon.com/dp/{asin}"
    try:
        voc_s = json.dumps(voc_data, ensure_ascii=False, indent=2)
    except TypeError:
        voc_s = json.dumps(str(voc_data), ensure_ascii=False)

    rufus_block = format_ask_rufus_for_gpt(orig.ask_rufus)
    kw = _keywords_display(orig.keywords)

    return f"""【对标与集群 VOC 数据（JSON）】
{voc_s}

{rufus_block}

【关键词（供 SEO 与搜索意图参考）】
{kw or "（无）"}

【产品链接（仅供理解类目与定位）】
{asin_link}

请严格遵守系统说明中的 JSON 顶层结构与各段字段要求，只输出 JSON，不要其它说明文字。"""


def call_chat_completion(user_content: str, system_prompt: Optional[str] = None) -> str:
    from openai import OpenAI

    key, base, model = _openai_settings()
    if not key:
        raise RuntimeError("未配置 OPENAI_API_KEY，请在环境变量或 Django settings 中设置。")
    client = OpenAI(base_url=base, api_key=key)
    sys_msg = system_prompt if (system_prompt and system_prompt.strip()) else SYSTEM_PROMPT
    r = client.chat.completions.create(
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_content},
        ],
        model=model,
        temperature=0.7,
    )
    return (r.choices[0].message.content or "").strip()


def parse_by_steps(raw: str) -> Dict[str, str]:
    markers = {
        "step1": r"【\s*对标\s*ASIN\s*分析\s*】",
        "step2": r"【\s*ASIN\s*集群\s*分析\s*】",
        "step3": r"【\s*差异化\s*分析\s*】",
    }
    found: Dict[str, int] = {}
    for key, pattern in markers.items():
        m = re.search(pattern, raw)
        if m:
            found[key] = m.start()

    if not found:
        return {"step1": raw, "step2": "", "step3": "", "step4": "", "step5": ""}

    ordered = sorted(found.items(), key=lambda x: x[1])
    sections: Dict[str, str] = {f"step{i}": "" for i in range(1, 6)}
    for i, (key, start) in enumerate(ordered):
        end = ordered[i + 1][1] if i + 1 < len(ordered) else len(raw)
        sections[key] = raw[start:end].strip()
    return sections


# 模型偶发把「产品描述」等同级字段写进「五点描述」数组，导致 JSON 无效
_LISTING_MISPLACED_KEYS = ("产品描述", "后台搜索词", "APlus内容建议", "广告与关键词策略")


def _repair_malformed_listing_json(text: str) -> str:
    """修复「五点描述」数组未闭合、同级字段误入数组的 Listing JSON。"""
    m = re.search(r'"五点描述"\s*:\s*\[', text)
    if not m:
        return text
    arr_start = m.end()
    best_pos: Optional[int] = None
    for key in _LISTING_MISPLACED_KEYS:
        km = re.search(rf'"{re.escape(key)}"\s*:', text[arr_start:])
        if not km:
            continue
        pos = arr_start + km.start()
        if "]" not in text[arr_start:pos]:
            if best_pos is None or pos < best_pos:
                best_pos = pos
    if best_pos is None:
        return text
    before = text[:best_pos].rstrip().rstrip(",")
    return before + "],\n" + text[best_pos:]


def _extract_listing_json_object(raw: str) -> Optional[Dict[str, Any]]:
    """解析 Listing JSON（含常见结构修复）。"""
    text = (raw or "").strip()
    if not text:
        return None
    parsed = _extract_json_object(text)
    if parsed:
        return parsed
    repaired = _repair_malformed_listing_json(text)
    if repaired != text:
        return _extract_json_object(repaired)
    return None


def _extract_json_object(raw: str) -> Optional[Dict[str, Any]]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    for pat in (
        r"```json\s*([\s\S]*?)\s*```",
        r"```\s*([\s\S]*?)\s*```",
    ):
        block = re.search(pat, text, flags=re.IGNORECASE)
        if block:
            inner = block.group(1).strip()
            try:
                data = json.loads(inner)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        chunk = text[start : end + 1]
        try:
            data = json.loads(chunk)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


def _pick_key(data: Dict[str, Any], candidates: List[str]) -> Optional[str]:
    def _norm(s: Any) -> str:
        return re.sub(r"[\s：:（）()\-_\u3000]+", "", str(s or "")).lower()

    for k in data.keys():
        ks = _norm(k)
        for c in candidates:
            if _norm(c) in ks or ks in _norm(c):
                return k
    return None


def _md_escape(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def _to_markdown_block(value: Any, level: int = 0) -> List[str]:
    lines: List[str] = []
    indent = "  " * level
    if isinstance(value, dict):
        for k, v in value.items():
            key = _md_escape(k)
            if isinstance(v, (dict, list)):
                lines.append(f"{indent}- **{key}**：")
                lines.extend(_to_markdown_block(v, level + 1))
            else:
                lines.append(f"{indent}- **{key}**：{_md_escape(v)}")
        return lines
    if isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{indent}-")
                lines.extend(_to_markdown_block(item, level + 1))
            else:
                lines.append(f"{indent}- {_md_escape(item)}")
        return lines
    lines.append(f"{indent}{_md_escape(value)}")
    return lines


_BENCHMARK_ORDER: List[Tuple[str, List[str]]] = [
    ("消费者画像", ["消费者画像", "人群画像", "用户画像"]),
    ("使用场景", ["使用场景", "场景"]),
    ("未被满足的需求", ["未被满足的需求", "未满足需求", "未被满足需求"]),
    ("好评", ["好评", "正面评价"]),
    ("差评", ["差评", "负面评价"]),
    ("购买动机", ["购买动机"]),
    ("建议和总结", ["建议和总结", "建议与总结", "总结与建议"]),
]


def _render_benchmark_block(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        body = obj.strip()
        if not body:
            return ""
        return "## 【对标 ASIN 分析】\n\n" + body
    if not isinstance(obj, dict):
        return "## 【对标 ASIN 分析】\n\n" + "\n".join(_to_markdown_block(obj))

    lines: List[str] = ["## 【对标 ASIN 分析】", ""]
    used: set[str] = set()
    for canonical, cands in _BENCHMARK_ORDER:
        pk = _pick_key(obj, cands)
        if not pk or pk in used:
            continue
        used.add(pk)
        val = obj.get(pk)
        lines.append(f"### {canonical}")
        lines.append("")
        if isinstance(val, (dict, list)):
            lines.extend(_to_markdown_block(val))
        else:
            lines.append(_md_escape(val) or "（空）")
        lines.append("")
    for k, v in obj.items():
        if k in used:
            continue
        lines.append(f"### {_md_escape(k)}")
        lines.append("")
        if isinstance(v, (dict, list)):
            lines.extend(_to_markdown_block(v))
        else:
            lines.append(_md_escape(v) or "（空）")
        lines.append("")
    return "\n".join(lines).strip()


def _render_cluster_block(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        body = obj.strip()
        if not body:
            return ""
        return "## 【ASIN 集群分析】\n\n" + body
    if not isinstance(obj, dict):
        return "## 【ASIN 集群分析】\n\n" + "\n".join(_to_markdown_block(obj))

    summary_candidates = ["建议和总结", "建议与总结", "总结与建议", "集群建议与总结"]
    lines: List[str] = ["## 【ASIN 集群分析】", ""]
    summary_key = _pick_key(obj, summary_candidates)
    head_keys = [k for k in obj.keys() if k != summary_key]
    for k in head_keys:
        v = obj.get(k)
        lines.append(f"### {_md_escape(k)}")
        lines.append("")
        if isinstance(v, (dict, list)):
            lines.extend(_to_markdown_block(v))
        else:
            lines.append(_md_escape(v) or "（空）")
        lines.append("")
    if summary_key:
        lines.append("### 建议和总结")
        lines.append("")
        sv = obj.get(summary_key)
        if isinstance(sv, (dict, list)):
            lines.extend(_to_markdown_block(sv))
        else:
            lines.append(_md_escape(sv) or "（空）")
    return "\n".join(lines).strip()


_DIFFERENTIATION_ORDER: List[Tuple[str, List[str]]] = [
    ("人群差异化", ["人群差异化"]),
    ("场景差异化", ["场景差异化"]),
    ("需求差异化", ["需求差异化"]),
    ("购买动机差异化", ["购买动机差异化"]),
    ("VOC定位", ["VOC定位", "VOC 定位", "voc定位"]),
    ("差评改进方向", ["差评改进方向", "差评改进", "差评与改进方向"]),
    ("结论与建议", ["结论与建议", "差异化结论", "总结"]),
]


def _render_differentiation_block(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        body = obj.strip()
        if not body:
            return ""
        return "## 【差异化分析】\n\n" + body
    if not isinstance(obj, dict):
        return "## 【差异化分析】\n\n" + "\n".join(_to_markdown_block(obj))

    lines: List[str] = ["## 【差异化分析】", ""]
    used: set[str] = set()
    for canonical, cands in _DIFFERENTIATION_ORDER:
        pk = _pick_key(obj, cands)
        if not pk or pk in used:
            continue
        used.add(pk)
        v = obj.get(pk)
        lines.append(f"### {canonical}")
        lines.append("")
        if isinstance(v, (dict, list)):
            lines.extend(_to_markdown_block(v))
        else:
            lines.append(_md_escape(v) or "（空）")
        lines.append("")
    for k, v in obj.items():
        if k in used:
            continue
        lines.append(f"### {_md_escape(k)}")
        lines.append("")
        if isinstance(v, (dict, list)):
            lines.extend(_to_markdown_block(v))
        else:
            lines.append(_md_escape(v) or "（空）")
        lines.append("")
    return "\n".join(lines).strip()


def extract_markdown_h3_body(text: str, title_candidates: List[str]) -> str:
    """
    从差异化分析等 Markdown 正文中，按「### 标题」抽取第一个匹配段落（到下一个 ### 或文末）。
    用于 Listing 面板等场景展示 VOC定位、差评改进方向等子块。
    """
    if not (text or "").strip():
        return ""
    t = text.strip()
    for raw_title in title_candidates:
        title = (raw_title or "").strip()
        if not title:
            continue
        pat = re.compile(
            rf"^###\s*{re.escape(title)}\s*\r?\n(.*?)(?=^###\s|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        m = pat.search(t)
        if m:
            return m.group(1).strip()
    return ""


def _json_to_sections(data: Dict[str, Any]) -> Dict[str, str]:
    """将模型返回的 JSON 转为 step1–step3 的 Markdown；step4/step5 留空。"""
    k_b = _pick_key(
        data,
        ["对标ASIN分析", "对标asin分析", "对标分析", "benchmark", "benchmark_voc"],
    )
    k_c = _pick_key(data, ["ASIN集群分析", "asin集群分析", "集群分析", "cluster_voc", "cluster"])
    k_d = _pick_key(data, ["差异化分析", "差异化", "differentiation"])

    sections: Dict[str, str] = {f"step{i}": "" for i in range(1, 6)}
    if k_b:
        sections["step1"] = _render_benchmark_block(data.get(k_b))
    if k_c:
        sections["step2"] = _render_cluster_block(data.get(k_c))
    if k_d:
        sections["step3"] = _render_differentiation_block(data.get(k_d))
    return sections


def _strip_step_heading(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"^\s*#+\s*【[^】]+】\s*\n?", "", text, count=1).strip()


def _has_meaningful_sections(sections: Dict[str, str]) -> bool:
    return any((sections.get(f"step{i}") or "").strip() for i in range(1, 4))


def save_analysis_by_steps(asin: str, sections: Dict[str, str], full_raw: str) -> AsinAnalysis:
    r1, r2, r3 = (
        sections.get("step1", "") or "",
        sections.get("step2", "") or "",
        sections.get("step3", "") or "",
    )
    s1, s2, s3 = _strip_step_heading(r1), _strip_step_heading(r2), _strip_step_heading(r3)
    # Listing 仅由 AI-Listing 页写入；新建 AsinAnalysis 时 listing 为空，重新分析已存在 ASIN 时不改 listing
    a, _ = AsinAnalysis.objects.get_or_create(
        asin=asin,
        defaults={"listing": ""},
    )
    AnalysisDetail.objects.filter(analysis=a).exclude(
        category__in=["benchmark", "cluster", "differentiation"]
    ).delete()

    mapping = {"benchmark": s1, "cluster": s2, "differentiation": s3}
    for cat, text in mapping.items():
        g = (text or "").strip()
        AnalysisDetail.objects.update_or_create(
            analysis=a,
            category=cat,
            defaults={"gpt_summary": g, "satisfy_condition": ""},
        )
    return a


def run_gpt_for_asin(asin: str) -> AsinAnalysis:
    orig = OriginalAsinData.objects.get(asin=asin)
    ok, err = validate_original_complete(orig)
    if not ok:
        raise ValueError(err)
    user = build_user_prompt(orig)
    raw = call_chat_completion(user)
    print(raw,'pppppppppppppppppppp')
    parsed_json = _extract_json_object(raw)
    if parsed_json:
        sections = _json_to_sections(parsed_json)
        if not _has_meaningful_sections(sections):
            sections = parse_by_steps(raw)
    else:
        sections = parse_by_steps(raw)
    return save_analysis_by_steps(asin, sections, raw)


LISTING_SYSTEM_PROMPT = (
    "你是一位资深亚马逊 Listing 优化专家，精通 Amazon A9/A10 SEO、消费者心理学、VOC 洞察、竞品分析与转化率优化。\n\n"

    "你的目标不是普通文案写作，而是生成【高转化率 + 高关键词相关性 + 高 Rufus 匹配度】的亚马逊 Listing。\n\n"

    "你必须严格遵守以下规则：\n"

    "【本消息内材料的关系（必须遵守）】\n"
    "用户消息按固定顺序给出多段材料，请依据这些段落写作，信息不足时可以自行获取正确信息。\n"
    "1）**关键词**、**Ask Rufus**：独立输入，分别用于 SEO/标题覆盖与显式回应消费者问答。\n"
    "2）**生成用凝结字段**：含 VOC定位、差评改进方向、差异化建议、材质与补充四块；"
    "其中前三项若与后文「三维分析报告」表述不一致，**以凝结字段为准**（其为人工确认后的定稿摘要）。\n"
    "3）**三维分析报告**含三节：**对标 ASIN 分析**、**ASIN 集群分析**、**差异化分析**。"
    "逻辑链：对标与集群分析是上游洞察，共同支撑 **VOC定位**；"
    "**差评改进方向** 写在差异化分析正文中，论据来自对标/集群 VOC；"
    "**差异化建议** 主要来自集群分析中的「建议和总结」类内容。\n"
    "写 Listing 时必须贯通 1）2）3），不得只抄凝结字段而忽略对标/集群报告中的事实、缺口与论据。\n"
    "某段为「（无）」时可结合其余段保守补全，禁止捏造参数、认证或竞品对比结论。\n\n"

    "【数据真实性规则】\n"
    "- 优先严格使用本消息中的事实、VOC、关键词、参数、竞品信息；材料不足时可做保守补全，不得编造。\n"
    "- 最好不要编造不存在的功能、材质、认证、销量、参数、实验结果、专利、对比结论或医疗功效。\n"
    "- 若 VOC 未提供百分比，可以在材料中适当虚构占比。\n"
    "- 若数据不足，使用保守表达，不允许幻想，但可以轻度夸大。\n\n"

    "【分析优先级】\n"
    "生成 Listing 时必须按以下优先级理解产品：\n"
    "1. VOC真实需求与差评痛点\n"
    "2. Ask Rufus 高频问题\n"
    "3. 搜索关键词SEO覆盖\n"
    "4. 竞品表达方式参考\n\n"

    "【SEO与转化规则】\n"
    "- 优先提升 CTR（点击率）与 CVR（转化率），而不是单纯描述产品。\n"
    "- 标题必须前置核心关键词。\n"
    "- Listing 必须自然覆盖核心关键词、场景词、长尾词。\n"
    "- 不允许关键词堆砌。\n"
    "- Listing 必须显式回应 Ask Rufus 高频问题。\n"
    "- 五点之间禁止重复表达。\n\n"

    "【语言规则】\n"
    "- Listing 内容必须使用美国站本地化英文。\n"
    "- JSON键名与说明结构使用中文。\n"
    "- 文案风格需符合美国消费者阅读习惯。\n\n"

    "【输出规则】\n"
    "- 输出必须是单个合法 JSON 对象。\n"
    "- 禁止输出 JSON 以外的任何文字。\n"
    "- 禁止使用 Markdown。\n"
    "- JSON 必须可被 json.loads() 正确解析。\n"
    "- 必须使用双引号。\n"
    "- 不允许尾逗号。\n\n"

    "顶层 JSON 键名必须完全一致：\n"

    '1. "Listing标题"\n'
    "- 字符串\n"
    "- 约 180–200 characters\n"
    "- 必须包含：\n"
    "  • 1个主关键词\n"
    "  • 1–2个次关键词\n"
    "  • 1-2个卖点词\n"
    "  • 至少1个场景词\n"
    "- 核心词前置\n"
    "- 如果后续的材质与补充当中有提到品牌名称信息，就要将品牌名称放到最前面\n"
    "- 如果后续有提到材料，那么标题当中就必须要包含材料\n\n"
    "- 包含差异化卖点或材质亮点\n\n"

    '2. "五点描述"\n'
    "- 长度必须为5的字符串数组\n"
    "- 每条约 150–200 characters\n"
    "- 第1点：差评与未满足需求的改进，可以适当夸大\n"
    "- 第2点：差异化卖点必须要包含对标asin与asin集群当中所有的购买动机，可以适当夸大\n"
    "- 第3点：明确回应 Ask Rufus 高频问题\n"
    "- 第4点：规格/材质/包装/售后\n"
    "- 第5点：VOC最核心需求\n"
    "- 每点允许包含1–2个核心英文关键词，每一点的内容要精益求精，展示精华部分\n\n"

    '3. "产品描述"\n'
    "- 字符串\n"
    "- 约 800–1500 characters\n"
    "- 必须包含：\n"
    "  • VOC痛点解决方案，可以适当夸大\n"
    "  • 差异化价值，可以适当夸大\n"
    "  • 包装/规格/售后\n"
    "  • 使用场景\n"
    "  • 人群动机，动机要包含对标asin和asin集群当中所有的购买动机\n"
    "- 使用短段落\n"
    "- 偏转化表达，不允许空洞描述\n\n"

    '4. "后台搜索词"\n'
    "- 字符串\n"
    "- 控制在250 bytes内\n"
    "- 使用英文\n"
    "- 包含长尾词、场景词、同义词\n"
    "- 不允许重复标题中已高频使用词\n"
    "- 不允许竞品品牌词\n\n"
    

    '5. "广告与关键词策略"\n'
    "- 对象\n"
    '- 必须包含键：\n'
    '  • "精准推广关键词"\n'
    '  • "品牌投放ASIN定位建议"\n'
    '  • "否定关键词"\n'
    "- 若数据不足，对应数组可为空数组，但键必须存在\n"
)


def _keywords_display(keywords: Any) -> str:
    if isinstance(keywords, str) and keywords.strip():
        return keywords.strip()
    if isinstance(keywords, list):
        return ", ".join(str(x) for x in keywords if x is not None and str(x).strip())
    return ""


def get_ai_listing_aux_field_values(
    orig: OriginalAsinData, details_by_cat: Dict[str, AnalysisDetail]
) -> Tuple[str, str, str, str]:
    """与生成 Listing 时一致的 VOC/差评/差异化/材质取值（可编辑字段优先，否则从分析 Markdown 抽取）。"""
    diff = details_by_cat.get("differentiation")
    cluster = details_by_cat.get("cluster")
    diff_md = (diff.gpt_summary or "").strip() if diff else ""
    cluster_md = (cluster.gpt_summary or "").strip() if cluster else ""
    voc = (getattr(orig, "voc_positioning_edited", None) or "").strip() or extract_markdown_h3_body(
        diff_md, ["VOC定位", "VOC 定位", "voc定位"]
    )
    neg = (getattr(orig, "negative_direction_edited", None) or "").strip() or extract_markdown_h3_body(
        diff_md, ["差评改进方向", "差评改进", "差评与改进方向"]
    )
    sug = (getattr(orig, "cluster_suggestion_edited", None) or "").strip() or extract_markdown_h3_body(
        cluster_md,
        ["建议和总结", "建议与总结", "总结与建议", "集群建议与总结"],
    )
    mat = (getattr(orig, "material_supplement", None) or "").strip() or "待定"
    return voc, neg, sug, mat


def _listing_edited_aux_block(orig: OriginalAsinData, details_by_cat: Dict[str, AnalysisDetail]) -> str:
    """凝结字段：可编辑值优先，未填则从下方三维分析报告的 Markdown 小节抽取。"""
    voc, neg, sug, mat = get_ai_listing_aux_field_values(orig, details_by_cat)
    return "\n".join(
        [
            "## 生成用凝结字段（定稿摘要，优先于后文同名小节）",
            "说明：下列四块为生成 Listing 时优先采用的浓缩结论；"
            "若与后文「差异化分析」「ASIN 集群分析」全文有出入，以本段为准。",
            "",
            "### VOC定位",
            voc or "（无）",
            "",
            "### 差评改进方向",
            neg or "（无）",
            "",
            "### 差异化建议",
            sug or "（无）",
            "",
            "### 材质与补充",
            mat,
            "",
        ]
    )


def record_ai_listing_generation(asin: str, listing_text: str, user: Any) -> None:
    """生成 Listing 成功后写入历史快照（失败或未调用则不应写入）。"""
    a = AsinAnalysis.objects.prefetch_related("details").filter(asin__iexact=asin).first()
    if not a:
        return
    canon = a.asin
    orig = OriginalAsinData.objects.filter(asin__iexact=canon).first()
    if not orig:
        return
    dm = {d.category: d for d in a.details.all()}
    voc, neg, sug, mat = get_ai_listing_aux_field_values(orig, dm)
    kw_raw = orig.keywords
    if kw_raw is None:
        kw_list: List[Any] = []
    elif isinstance(kw_raw, list):
        kw_list = kw_raw
    else:
        kw_list = [kw_raw]
    ar = orig.ask_rufus if isinstance(orig.ask_rufus, dict) else {}
    username = ""
    uid: Optional[int] = None
    if user is not None and getattr(user, "is_authenticated", False):
        username = (getattr(user, "get_username", lambda: "")() or getattr(user, "username", "") or "").strip()
        uid = int(user.pk)
    AiListingGenerationHistory.objects.create(
        asin=canon,
        generated_by_id=uid,
        generated_by_username=username,
        keywords=kw_list,
        ask_rufus=ar,
        voc_positioning=voc,
        negative_direction=neg,
        cluster_suggestion=sug,
        material_supplement=mat,
        listing=listing_text or "",
    )


def build_ai_listing_user_prompt(
    orig: OriginalAsinData,
    details_by_cat: Dict[str, AnalysisDetail],
    user_notes: str,
) -> str:
    asin = orig.asin
    link = f"https://www.amazon.com/dp/{asin}"
    kw = _keywords_display(orig.keywords)
    ruf_s = format_ask_rufus_for_gpt(orig.ask_rufus)

    def _blk(label: str, cat: str) -> str:
        d = details_by_cat.get(cat)
        if not d:
            return f"### {label}\n（无此维度记录）\n"
        sat = (d.satisfy_condition or "").strip() or "（未填写）"
        summ = (d.gpt_summary or "").strip()
        # summ = summ[:12000] + ("…" if len(summ) > 12000 else "")
        return f"### {label}\n**满足条件（人工填写）**：\n{sat}\n\n**GPT 分析摘要**：\n{summ}\n"

    material_map = (
        "## 本消息材料结构说明（请按下列顺序阅读并贯通使用）\n"
        "下文各节标题与正文一一对应，请严格依据各节内容写作，勿假设消息外还有未给出的数据。\n\n"
        "| 顺序 | 本节标题 | 作用与关系 |\n"
        "|---|---|---|\n"
        "| 1 | **关键词** | 搜索词列表，用于标题/五点/描述中的 SEO 自然覆盖。 |\n"
        "| 2 | **Ask Rufus** | 买家「问题」及 Rufus「解答」成对给出；Listing 须显式回应问题关切，并可参考解答中的卖点表述。 |\n"
        "| 3 | **生成用凝结字段** | 四块定稿摘要：VOC定位、差评改进方向、差异化建议、材质与补充；"
        "与后文同名内容冲突时**以本节为准**。 |\n"
        "| 4 | **对标 ASIN 分析** | 对标竞品 VOC 与洞察的**完整报告**（上游材料之一）。 |\n"
        "| 5 | **ASIN 集群分析** | 集群竞品 VOC 与缺口/建议的**完整报告**；「建议和总结」类内容支撑**差异化建议**。 |\n"
        "| 6 | **差异化分析** | 含 **VOC定位**、**差评改进方向** 等小结的**完整报告**；"
        "VOC定位 由对标+集群分析归纳支撑，写文案时须同时呼应第 4、5 节事实。 |\n"
        "| 7 | **用户补充说明** | 人工追加要求，须重点落实。 |\n\n"
        "**逻辑关系（写作时内化，勿只抄第 3 节）**：\n"
        "- 第 4、5 节 → 共同支撑第 6 节及第 3 节中的 **VOC定位**。\n"
        "- **差评改进方向**：第 3 节为摘要，论据见第 4、5、6 节。\n"
        "- **差异化建议**：第 3 节为摘要，详细论据见第 5 节集群报告。\n"
        "- **材质与补充**：仅见第 3 节，须写入 Listing 规格/包装/售后等相关表述。\n"
        "- 贯通第 1–7 节；任一节为「（无）」时可据其余节保守补全，禁止捏造参数、认证或竞品对比。\n"
    )

    parts = [
        f"ASIN：{asin}",
        f"产品链接：{link}",
        "",
        material_map,
        "",
        f"### 关键词\n{kw or '（无）'}",
        "",
        f"### Ask Rufus（问题与解答）\n{ruf_s}",
        "",
        _listing_edited_aux_block(orig, details_by_cat),
        "## 三维分析报告（完整正文）",
        _blk("对标 ASIN 分析", "benchmark"),
        _blk("ASIN 集群分析", "cluster"),
        _blk("差异化分析", "differentiation"),
        "",
        "## 用户补充说明",
        (user_notes or "").strip() or "（无补充）",
    ]
    return "\n".join(parts)


def format_listing_output(raw: str) -> str:
    """若模型返回可解析 JSON，则规范缩进写入 listing；否则保留原文。"""
    text = (raw or "").strip()
    if not text:
        return ""
    parsed = _extract_listing_json_object(text)
    if parsed:
        try:
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            pass
    return text


LISTING_MD_KEYS_ORDER = [
    "Listing标题",
    "五点描述",
    "产品描述",
    "后台搜索词",
    "广告与关键词策略",
]


def _listing_value_to_markdown(value: Any, depth: int = 0) -> List[str]:
    """将 Listing JSON 中的字段值转为 Markdown 行。"""
    lines: List[str] = []
    if value is None:
        return lines
    if isinstance(value, str):
        for para in value.split("\n"):
            p = para.strip()
            if p:
                lines.append(p)
        lines.append("")
        return lines
    if isinstance(value, list):
        for i, item in enumerate(value, 1):
            if isinstance(item, dict):
                lines.append(f"{i}.")
                for sub_k, sub_v in item.items():
                    sub_lines = _listing_value_to_markdown(sub_v, depth + 1)
                    body = "\n".join(sub_lines).strip()
                    if body:
                        lines.append(f"   - **{sub_k}**：{body.replace(chr(10), ' ')}")
                    else:
                        lines.append(f"   - **{sub_k}**")
                lines.append("")
            elif isinstance(item, (dict, list)):
                nested = "\n".join(_listing_value_to_markdown(item, depth + 1)).strip()
                lines.append(f"{i}. {nested}" if nested else f"{i}.")
                lines.append("")
            else:
                lines.append(f"{i}. {item}")
        if lines and lines[-1] != "":
            lines.append("")
        return lines
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                lines.append(f"### {k}")
                lines.append("")
                lines.extend(_listing_value_to_markdown(v, depth + 1))
            else:
                lines.append(f"**{k}**：{v}")
                lines.append("")
        return lines
    lines.append(str(value))
    lines.append("")
    return lines


def listing_json_to_markdown(obj: Dict[str, Any]) -> str:
    """将 AI-Listing 的 JSON 结构转为可读 Markdown（供面板展示）。"""
    lines: List[str] = []
    seen: set[str] = set()
    for key in LISTING_MD_KEYS_ORDER:
        if key not in obj:
            continue
        seen.add(key)
        lines.append(f"## {key}")
        lines.append("")
        lines.extend(_listing_value_to_markdown(obj[key]))
    for key, val in obj.items():
        if key in seen:
            continue
        lines.append(f"## {key}")
        lines.append("")
        lines.extend(_listing_value_to_markdown(val))
    return "\n".join(lines).strip()


def listing_raw_to_markdown(raw: str) -> str:
    """Listing 字段原文：能解析为 JSON 则转 Markdown，否则按原 Markdown 文本返回。"""
    text = (raw or "").strip()
    if not text:
        return ""
    parsed = _extract_listing_json_object(text)
    if parsed:
        return listing_json_to_markdown(parsed)
    return text


def run_ai_listing_for_asin(asin: str, user_notes: str) -> AsinAnalysis:
    """
    基于已有三条 AnalysisDetail（含满足条件与 GPT 摘要）及用户输入，生成 Listing 写入 AsinAnalysis.listing。
    """
    a = (
        AsinAnalysis.objects.prefetch_related("details")
        .filter(asin=asin)
        .first()
    )
    if not a:
        raise ValueError(f"未找到 ASIN {asin} 的分析记录，请先在「AI 差异化分析」中完成 VOC 分析。")
    orig = OriginalAsinData.objects.filter(asin__iexact=asin).first()
    if not orig:
        raise ValueError(f"原文本库中不存在 ASIN：{asin}")

    dm = {d.category: d for d in a.details.all()}
    if not dm:
        raise ValueError("该 ASIN 尚无分析明细，请先完成差异化分析。")

    user_content = build_ai_listing_user_prompt(orig, dm, user_notes)
    print(user_content,'111111111111')
    raw = call_chat_completion(user_content, system_prompt=LISTING_SYSTEM_PROMPT)
    print(raw,'222222222')
    text = format_listing_output(raw)
    if not text:
        raise RuntimeError("模型未返回 Listing 内容。")
    a.listing = text
    a.save(update_fields=["listing"])
    return a
