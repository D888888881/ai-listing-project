import json
from openai import OpenAI

import os

# ================== 配置 ==================
API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai-proxy.org/v1")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.1")
if not API_KEY:
    raise RuntimeError("请设置环境变量 OPENAI_API_KEY")


# # ================== 读取 VOC 数据 ==================
# with open(VOC_FILE, "r", encoding="utf-8") as f:
#     voc_data = json.load(f)

# ================== 构造提示词 ==================
system_prompt = (
    "你是一位资深亚马逊产品分析师、消费者洞察专家"
)

user_prompt = f"""帮我分析"https://www.amazon.com/dp/B0FWJ8HNCB"这个链接的产品
"""

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
    model=MODEL
)

# ================== 输出结果 ==================
print(chat_completion.choices[0].message.content)
