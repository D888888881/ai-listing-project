import os

import openai
import requests


def _openai_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("请设置环境变量 OPENAI_API_KEY")
    return key


def request_chatgpt_function():
    url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    openai_api_key = _openai_api_key()
    header = {"Content-Type": "application/json", "Authorization": f"Bearer {openai_api_key}"}
    data = {
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o"),
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ],
        "temperature": 0,
        "stream": False,
    }
    response = requests.post(url=url, headers=header, json=data, timeout=120).json()
    print(response)
    return response


def openai_chatgpt_function():
    question = "西游记是谁写的？"
    print(f"问题:{question}")
    url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai.api_key = _openai_api_key()
    openai.api_base = url
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": question}],
        stream=False,
    )
    print(f"完整的响应结果:{response}")
    answer = response.choices[0].message.content
    print(f"答案:{answer}")


if __name__ == "__main__":
    request_chatgpt_function()
