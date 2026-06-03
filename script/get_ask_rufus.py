import asyncio
import aiohttp

WEBHOOK_URL = "http://192.168.93.129:5678/webhook/9392a935-f245-4c87-8ca1-da1e57c6df0d"

async def fetch_rufus_questions(session: aiohttp.ClientSession, asin_list: list[str]) -> dict:
    """
    向 webhook 发送 GET 请求，返回 Rufus 问题数据。
    :param session: aiohttp 会话
    :param asin_list: ASIN 列表，例如 ["B0F6MTPQVG", "B0G5Y7G4M3"]
    :return: 解析后的 JSON 字典
    """
    message = ",".join(asin_list)
    params = {"message": message}
    try:
        async with session.get(WEBHOOK_URL, params=params, timeout=120) as response:
            response.raise_for_status()
            return await response.json()
    except Exception as e:
        print(f"请求失败 (ASIN: {message}): {e}")
        return {}

async def main_ask_rufus(asins):
    # 要查询的 ASIN 列表，可以自行修改或从命令行参数读取


    async with aiohttp.ClientSession() as session:
        result = await fetch_rufus_questions(session, asins)
        print(result,'2222')

    print("获取到的 Rufus 问题：")
    for asin, questions in result.items():
        print(f"\n{asin}:")
        if not isinstance(questions, dict):
            print(f"  (非对象): {questions}")
            continue
        for key, value in questions.items():
            if isinstance(value, dict):
                for q_text, full_text in value.items():
                    print(f"  {key}: {q_text}")
                    preview = str(full_text or "").replace("\n", " ")[:120]
                    print(f"    → {preview}…")
            else:
                print(f"  {key}: {value}")
    return result
if __name__ == "__main__":
    result = asyncio.run(main_ask_rufus(["B0F6MTPQVG"]))
    print(result)