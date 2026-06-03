import os

from openai import OpenAI

api_key = os.environ.get("ZEOAI_API_KEY", "").strip()
if not api_key:
    raise RuntimeError("请设置环境变量 ZEOAI_API_KEY")

client = OpenAI(
    base_url=os.environ.get("ZEOAI_BASE_URL", "https://www.zeoapi.com/v1"),
    api_key=api_key,
)

resp = client.chat.completions.create(
    model="claude-opus-4-6-thinking",
    messages=[{"role": "user", "content": "Hello"}],
    max_tokens=1024
)
print(resp)