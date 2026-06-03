import asyncio
import os
import time
from typing import List, Dict, Any, Optional

import aiohttp


class Helium10AsyncAPI:
    """
    异步并发版本的 Helium10 Cerebro API 客户端
    支持同时查询多个 ASIN 的关键词数据，内部对每个 ASIN 先创建搜索再拉取结果
    """

    BASE_URL = "https://h10api.pacvue.com"

    def __init__(
        self,
        authorization_token: str,
        x_pacvue_token: str,
        account_id: str = "1547528739",
        user_agent: Optional[str] = None,
        max_concurrent: int = 10,          # 全局最大并发数（批量查询时使用）
    ):
        """
        :param authorization_token: JWT 令牌（用于 Authorization 头）
        :param x_pacvue_token:          另一个 JWT 令牌（用于 x-pacvue-token 头）
        :param account_id:              Helium10 账户 ID（用于请求参数）
        :param user_agent:              自定义 User-Agent
        :param max_concurrent:          批量请求时的最大并发数
        """
        self.auth_token = authorization_token
        self.x_pacvue_token = x_pacvue_token
        self.account_id = account_id
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0"
        )
        self.max_concurrent = max_concurrent

        # 基础请求头（每次请求都会带上）
        self.base_headers = {
            "Host": "h10api.pacvue.com",
            "sec-ch-ua-platform": '"Windows"',
            "authorization": f"Bearer {self.auth_token}",
            "sec-ch-ua": '"Microsoft Edge";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "x-pacvue-token": f"Bearer {self.x_pacvue_token}",
            "user-agent": self.user_agent,
            "accept": "application/json",
            "content-type": "application/json",
            "origin": "https://members.helium10.com",
            "sec-fetch-site": "cross-site",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "referer": "https://members.helium10.com/cerebro-new?accountId=1547528739",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "priority": "u=1, i",
        }

        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore: Optional[asyncio.Semaphore] = None

    async def __aenter__(self):
        """进入运行时上下文，创建 aiohttp 会话和并发信号量"""
        self._session = aiohttp.ClientSession()
        self._session.headers.update(self.base_headers)
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出运行时上下文，关闭会话"""
        if self._session:
            await self._session.close()
            self._session = None

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        通用 HTTP 请求方法
        :param method:  GET 或 POST
        :param path:    URL 路径，如 /rta/cerebro/v1/amazon/search/single
        :param params:  URL 查询参数
        :param json_data: JSON 请求体（POST 时使用）
        :return:         解析后的 JSON 字典
        """
        if not self._session:
            raise RuntimeError(
                "请在异步上下文管理器中使用 Helium10AsyncAPI，"
                "例如 'async with Helium10AsyncAPI(...) as api:'"
            )

        url = f"{self.BASE_URL}{path}"
        async with self._session.request(
            method=method,
            url=url,
            params=params,
            json=json_data,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def create_search(self, asin: str) -> str:
        """
        为指定 ASIN 创建一个 Cerebro 搜索，返回 search_id
        """
        path = "/rta/cerebro/v1/amazon/search/single"
        params = {"accountId": self.account_id}
        body = {
            "marketplace": "ATVPDKIKX0DER",
            "productId": asin,
            "adminSearch": False,
            "exactProduct": False,
        }
        resp_json = await self._request("POST", path, params=params, json_data=body)
        return resp_json["data"]["id"]

    async def get_search_keywords(
        self,
        search_id: str,
        include_all: str = "0",
        include_any: str = "1",
        page: str = "1",
        per_page: str = "50",
        sort: str = "default",
    ) -> List[Dict[str, Any]]:
        """
        根据 search_id 拉取关键词数据（tableData）
        :return: 关键词行列表，每行至少包含 phrase 字段
        """
        path = f"/rta/cerebro/v1/amazon/search/single/{search_id}/data"
        params = {
            "accountId": self.account_id,
            "include-all": include_all,
            "include-any": include_any,
            "includeAny": include_any,          # 原请求中同时存在两个同名参数
            "page": page,
            "per_page": per_page,
            "sort": sort,
        }
        resp_json = await self._request("GET", path, params=params)
        return resp_json.get("data", {}).get("tableData", [])

    async def fetch_one_asin(self, asin: str) -> List[Dict[str, Any]]:
        """
        查询单个 ASIN 的完整流程：创建搜索 -> 获取关键词列表
        返回关键词行列表（tableData），异常时返回空列表
        """
        try:
            search_id = await self.create_search(asin)
            keywords = await self.get_search_keywords(search_id)
            return keywords[:20]
        except Exception as e:
            print(f"查询 ASIN {asin} 失败: {e}")
            return []

    async def fetch_multiple_asins(
        self,
        asins: List[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        并发查询多个 ASIN 的关键词数据
        :param asins: ASIN 列表
        :return: 字典 {asin: [keyword_row_dict, ...]}
        """
        async def _one(asin: str) -> tuple[str, List[Dict[str, Any]]]:
            async with self._semaphore:
                data = await self.fetch_one_asin(asin)
                return asin, data

        tasks = [_one(asin) for asin in asins]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        final_dict: Dict[str, List[Dict[str, Any]]] = {}
        for item in results:
            if isinstance(item, Exception):
                print(f"批量请求异常: {item}")
                continue
            asin, data = item
            final_dict[asin] = data
        return final_dict


# ==================== 使用示例 ====================
def _resolve_h10_tokens_sync() -> tuple[str, str]:
    """同步读取凭证（供脚本独立运行或非 async 上下文使用）。"""
    try:
        import django
        from django.conf import settings as dj_settings

        if not dj_settings.configured:
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai_listing_project.settings")
            django.setup()
        from myapp.h10_config import get_h10_credentials

        return get_h10_credentials()
    except Exception as exc:
        auth = os.environ.get("H10_AUTH_TOKEN", "").strip()
        x_token = os.environ.get("H10_X_TOKEN", "").strip()
        if auth and x_token:
            return auth, x_token
        from myapp.h10_config import H10CredentialMissingError

        if isinstance(exc, H10CredentialMissingError):
            raise exc
        raise RuntimeError(
            "Helium10 凭证未配置。请由超级管理员在「设置 → Helium10 凭证」中填写，"
            "或设置环境变量 H10_AUTH_TOKEN / H10_X_TOKEN。"
        ) from exc


async def _resolve_h10_tokens() -> tuple[str, str]:
    """在 async 上下文中通过 sync_to_async 读取 Django 数据库凭证。"""
    from asgiref.sync import sync_to_async
    from myapp.h10_config import H10CredentialMissingError, get_h10_credentials

    try:
        return await sync_to_async(get_h10_credentials, thread_sensitive=True)()
    except H10CredentialMissingError:
        auth = os.environ.get("H10_AUTH_TOKEN", "").strip()
        x_token = os.environ.get("H10_X_TOKEN", "").strip()
        if auth and x_token:
            return auth, x_token
        raise


async def h10_main(asins, auth_token: str | None = None, x_pacvue_token: str | None = None):
    if auth_token and x_pacvue_token:
        auth, x_token = auth_token, x_pacvue_token
    else:
        auth, x_token = await _resolve_h10_tokens()

    async with Helium10AsyncAPI(
        authorization_token=auth,
        x_pacvue_token=x_token,
        max_concurrent=5,
    ) as api:
        # 批量查询
        multi_results = await api.fetch_multiple_asins(asins)

        # 提取关键词短语（与原同步代码行为一致）
        for asin, rows in multi_results.items():
            phrases = [row["phrase"] for row in rows]
            print(f"{asin}: {phrases}")

        # 如果需要只返回短语列表的字典，可以这样构造
        phrase_dict = {asin: [r["phrase"] for r in rows] for asin, rows in multi_results.items()}
        return phrase_dict


if __name__ == "__main__":
    result = asyncio.run(h10_main(["B0F6MTPQVG", "B0FWJ8HNCB"]))
    print("\n最终短语字典:", result)