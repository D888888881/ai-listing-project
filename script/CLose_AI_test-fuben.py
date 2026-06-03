import json
from openai import OpenAI

# ================== 配置 ==================
import os

API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
if not API_KEY:
    raise RuntimeError("请设置环境变量 OPENAI_API_KEY")
BASE_URL = "https://api.openai-proxy.org/v1"
MODEL = "gpt-5.4-nano"
VOC_FILE = "B01B8R6PF2_VOC.json"  # VOC 文件路径
ASIN_LINK = "https://www.amazon.com/dp/B01B8R6PF2"

# Rufus 问题（你提供的 5 个问题）
RUFUS_QUESTIONS = {
    "question1": "Can these batteries be used in remote controls?",
    "question2": "Do they work well in cold temperatures?",
    "question3": "Are they made with recycled materials?",
    "question4": "Why you might like this",
    "question5": "Compare with similar"
}

# 关键词
KEYWORDS = "aa batteries, batteries, double a batteries, batteries aa size pack, batteries aa"

# ================== 读取 VOC 数据 ==================
with open(VOC_FILE, "r", encoding="utf-8") as f:
    voc_data = json.load(f)

# ================== 构造提示词 ==================
system_prompt = (
    "你是一位资深亚马逊产品分析师、消费者洞察专家和Listing转化优化专家。\n\n"

    "你将收到：\n"
    "1. VOC数据（包含消费者画像、使用场景、未被满足需求、好评、差评、购买动机）\n"
    "2. Ask Rufus 买家问题列表\n"
    "3. 产品关键词\n"
    "4. 亚马逊竞品链接（用于理解市场定位）\n\n"

    "你的目标不是做普通分析，而是输出【可直接提升转化率的Listing方案】。\n\n"

    "请严格按照以下结构输出：\n\n"

    "【第一步：Rufus需求拆解】\n"
    "- 提取每个问题背后的真实购买动机（不仅是表面问题）\n"
    "- 标注需求优先级（高/中/低）\n"
    "- 输出：\n"
    "  1）需求总结（必须用列表）\n"
    "  2）对应Listing优化建议（标题/五点/图片/A+分别说明）\n\n"

    "【第二步：差评与未满足需求分析】\n"
    "- 只基于VOC，不允许编造数据\n"
    "- 提取核心痛点（按影响转化排序）\n"
    "- 明确：哪些会导致退货/差评\n"
    "- 输出：\n"
    "  1）Top痛点（按优先级排序）\n"
    "  2）每个痛点对应的Listing改进策略\n"
    "  3）需要避免的错误表达（反向建议）\n\n"

    "【第三步：人群与场景驱动分析（必须结合百分比）】\n"
    "- 所有百分比必须来自VOC原始数据，不允许虚构\n"
    "- 输出必须包含表格，并按权重排序\n"
    "- 分析维度包括：\n"
    "  1）人群结构\n"
    "  2）使用时刻\n"
    "  3）使用地点\n"
    "  4）行为与购买动机\n\n"
    "- 输出：\n"
    "  1）核心人群优先级排序\n"
    "  2）核心使用场景排序\n"
    "  3）Listing主打卖点方向（基于权重）\n\n"

    "【第四步：差异化策略】\n"
    "- 给出5条明确可执行差异化策略\n"
    "- 必须包含：\n"
    "  1）定位差异\n"
    "  2）卖点差异\n"
    "  3）视觉/主图差异\n"
    "  4）对比竞品策略\n"
    "  5）风险规避建议\n\n"

    "【第五步：最终Listing生成】\n"
    "必须符合以下规则：\n"
    "- 标题：180-200字符，必须包含核心关键词\n"
    "- 五点：每条有清晰卖点，不允许重复\n"
    "- 描述：偏转化表达，不要空话\n"
    "- A+：必须给结构（模块划分）\n\n"

    "【强约束规则】\n"
    "- 不允许编造VOC中不存在的百分比\n"
    "- 不允许虚构产品功能\n"
    "- 不允许使用医疗/夸大承诺（如 cure / guaranteed）\n"
    "- 必须优先考虑转化率，而不是泛泛分析\n"
    "- 输出必须结构清晰，可直接用于商业落地\n"
)

user_prompt = f"""以下是产品数据：

【VOC 消费者画像数据】
{json.dumps(voc_data, ensure_ascii=False, indent=2)}

【Ask Rufus 买家常见问题】
{json.dumps(RUFUS_QUESTIONS, ensure_ascii=False, indent=2)}

【关键词】
{KEYWORDS}

【亚马逊产品链接】
{ASIN_LINK}

请根据以下五步流程进行分析，并最终生成五条差异化建议及完整 listing。分析结果需要以 JSON 格式输出，结构要求如下：

1. **第一步：Rufus 问题分析**
   - 对每个 Rufus 问题进行深入分析，提炼出买家的核心需求。
   - 每个问题要输出以下字段：
     ```json
     {{
"question": "问题内容",
       "detailed_analysis": "问题背后的核心需求分析",
       "listing_suggestion": "针对该问题的 Listing 优化建议"
     }}
     ```

2. **第二步：差评与未被满足的需求分析**
   - 分析 VOC 中的差评和未被满足的需求，提取出影响购买决策的核心痛点。
   - 输出以下字段：
     ```json
     {{
"pain_points": [
         {{
"issue": "痛点描述",
           "impact": "该痛点对转化率的影响",
           "listing_improvement": "针对该痛点的 Listing 改进建议"
         }},
         ...
       ]
     }}
     ```

3. **第三步：人群特征与使用场景分析**
   - 分析 VOC 中的使用场景、购买动机、人群特征等，并基于百分比权重给出精准的 Listing 文案方向。
   - 输出以下字段：
     ```json
     {{
"target_audience": [
         {{
"group": "人群名称",
           "percentage": "占比",
           "priority": "该人群的优先级"
         }},
         ...
       ],
       "usage_scenarios": [
         {{
"scenario": "使用场景",
           "percentage": "占比",
           "importance": "该场景的优先级"
         }},
         ...
       ],
       "suggested_copy_direction": "基于上述分析给出的文案方向"
     }}
     ```

4. **第五步：差异化策略**
   - 给出五条具体的差异化策略，帮助该产品与竞争产品脱颖而出：
     ```json
     {{
"differentiation_strategies": [
         "差异化策略 1",
         "差异化策略 2",
         ...
       ]
     }}
     ```

5. **生成最终 Listing**
   - 请根据分析结果生成最终的亚马逊 Listing，包括以下字段：
     ```json
     {{
"title": "产品标题",
       "bullet_points": [
         "五点描述 1",
         "五点描述 2",
         ...
       ],
       "description": "商品描述",
       "a_plus_modules": [
         "A+模块 1",
         "A+模块 2",
         ...
       ]
     }}
     ```

请根据以上结构化要求输出 JSON 格式的分析结果，确保每一步都包含完整的分析和建议。"""

# ================== 调用 API ==================
client = OpenAI(
    base_url=BASE_URL,
    api_key=API_KEY,
)

chat_completion = client.chat.completions.create(
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ],
    model=MODEL,
    temperature=0.7,  # 可适当调整创意程度
)

# ================== 输出结果 ==================
print(chat_completion.choices[0].message.content)
