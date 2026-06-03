import asyncio
import os
import aiohttp
import time

from typing import List, Dict, Any, Optional, Tuple


def _normalize_cpc_row(cpc: Any) -> Optional[Dict[str, Any]]:
    """
    接口有时返回 cpc 为 dict，有时为单元素列表 [{...}]；统一成一层 dict。
    """
    if cpc is None:
        return None
    if isinstance(cpc, dict):
        return cpc
    if isinstance(cpc, list):
        for item in cpc:
            if isinstance(item, dict):
                return item
        return None
    return None


def _coerce_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _first_usable_cpc_slot(cpc_any: Any) -> Optional[Dict[str, Any]]:
    """
    真值通常在 cpc['legacyForSales_exact'][0]（含类目与 start/median/end），
    顶层可能没有 median/categoryid。取第一条可用的 dict；若为数字列表则展开为区间占位。
    """
    cpc_d = _normalize_cpc_row(cpc_any)
    if not cpc_d:
        return None
    leg = cpc_d.get("legacyForSales_exact")
    if isinstance(leg, (list, tuple)) and len(leg) > 0:
        first = leg[0]
        if isinstance(first, dict):
            if any(
                first.get(k) is not None
                for k in ("median", "start", "end", "categoryid", "categoryName")
            ):
                return first
        else:
            try:
                v = float(first)
                return {
                    "categoryid": cpc_d.get("categoryid"),
                    "categoryName": cpc_d.get("categoryName"),
                    "start": v,
                    "median": v,
                    "end": v,
                }
            except (TypeError, ValueError):
                pass
    if any(
        cpc_d.get(k) is not None
        for k in ("median", "start", "end", "categoryid", "categoryName")
    ):
        return {
            "categoryid": cpc_d.get("categoryid"),
            "categoryName": cpc_d.get("categoryName"),
            "start": cpc_d.get("start"),
            "median": cpc_d.get("median"),
            "end": cpc_d.get("end"),
        }
    return None


def _slot_to_cpc_output(slot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "categoryid": slot.get("categoryid"),
        "categoryName": slot.get("categoryName"),
        "start": _coerce_float(slot.get("start")),
        "median": _coerce_float(slot.get("median")),
        "end": _coerce_float(slot.get("end")),
    }


def _slot_comparison_price(slot: Dict[str, Any]) -> Optional[float]:
    """比较多条关键词的「cpc 价」：优先 median，其次 (start+end)/2，再 start/end。"""
    m = _coerce_float(slot.get("median"))
    if m is not None:
        return m
    s = _coerce_float(slot.get("start"))
    e = _coerce_float(slot.get("end"))
    if s is not None and e is not None:
        return (s + e) / 2.0
    if s is not None:
        return s
    if e is not None:
        return e
    return None


def _scale_cpc_prices(slot: Dict[str, Any], factor: float) -> Dict[str, Any]:
    """对竞价区间乘系数（如 0.8），类目字段原样保留。"""
    out: Dict[str, Any] = {
        "categoryid": slot.get("categoryid"),
        "categoryName": slot.get("categoryName"),
        "start": None,
        "median": None,
        "end": None,
    }
    for k in ("start", "median", "end"):
        v = _coerce_float(slot.get(k))
        out[k] = v * factor if v is not None else None
    return out


def aggregate_sif_keyword_rows(
    rows: List[Dict[str, Any]],
    top_n: int = 10,
    cpc_price_factor: float = 0.8,
) -> Dict[str, Any]:
    """
    在至多 top_n 条（默认 10）关键词里，若不足 10 条则用全部；
    遍历这些行的 cpc（legacy 槽位），取「比价」最高的一条的类目与区间，
    再将 start/median/end 乘以 cpc_price_factor（默认 0.8）。
    clickPurchaseRatio 取胜出那一行的转化率。
    """
    empty = {
        "clickPurchaseRatio": None,
        "cpc": {
            "categoryid": None,
            "categoryName": None,
            "start": None,
            "median": None,
            "end": None,
        },
    }
    window = (rows or [])[: max(0, top_n)]
    if not window:
        return empty

    best: Optional[Tuple[float, Dict[str, Any], Dict[str, Any]]] = None
    for row in window:
        slot = _first_usable_cpc_slot(row.get("cpc"))
        if not slot:
            continue
        price = _slot_comparison_price(slot)
        if price is None:
            continue
        if best is None or price > best[0]:
            best = (price, row, slot)

    if best is None:
        return empty

    _, win_row, win_slot = best
    ratio = _coerce_float(win_row.get("clickPurchaseRatio"))
    return {
        "clickPurchaseRatio": ratio,
        "cpc": _scale_cpc_prices(win_slot, cpc_price_factor),
    }


class AsyncSifAPI:
    """
    异步并发版本的 SifAPI 客户端
    使用 aiohttp 实现，支持在同一会话中并发执行多个请求
    """

    BASE_URL = "https://www.sif.com/api"

    def __init__(
        self,
        authorization_token: str,
        cookies: Dict[str, str],
        client_id: Optional[str] = None,
        user_agent: Optional[str] = None,
        max_concurrent: int = 10  # 全局最大并发请求数（用于批量方法）
    ):
        """
        :param authorization_token: JWT 令牌（用于 authorization 头）
        :param cookies: 包含登录态 Cookie 的字典（如 sif_token 等）
        :param client_id: 客户端标识 _m，默认使用从原代码提取的值
        :param user_agent: 自定义 User-Agent
        :param max_concurrent: 批量请求时最大并发数
        """
        self.auth_token = authorization_token
        self.cookies = cookies
        self.client_id = client_id or "Sif_d88a-a869-4154-b5dd-3ed9-1773713262017"
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
        )
        self.max_concurrent = max_concurrent

        # 基础请求头（不含动态 referer）
        self.base_headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Connection": "keep-alive",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://www.sif.com",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": self.user_agent,
            "authorization": self.auth_token,
            "sec-ch-ua": (
                '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"'
            ),
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"'
        }

        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore: Optional[asyncio.Semaphore] = None

    async def __aenter__(self):
        """进入上下文时创建 ClientSession，设置 cookies 和基础 headers"""
        self._session = aiohttp.ClientSession()
        # 设置 cookies
        self._session.cookie_jar.update_cookies(self.cookies)
        # 设置基础 headers（后续每个请求可临时覆盖）
        self._session.headers.update(self.base_headers)
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出上下文时关闭 session"""
        if self._session:
            await self._session.close()
            self._session = None

    def _build_params(self, country: str = "US") -> Dict[str, str]:
        """生成公共查询参数（含动态时间戳）"""
        return {
            "country": country,
            "_t": str(int(time.time() * 1000)),  # 毫秒级时间戳
            "_m": self.client_id
        }

    async def _post(
        self,
        endpoint: str,
        data: Dict[str, Any],
        country: str = "US",
        referer: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        发送 POST 请求的内部方法，返回解析后的 JSON 字典
        :param endpoint: API 路径
        :param data: 请求体（自动 JSON 序列化）
        :param country: 国家代码
        :param referer: 可选的 Referer 头，若提供则临时覆盖
        :return: 服务器返回的 JSON 字典
        """
        if not self._session:
            raise RuntimeError("请在异步上下文管理器中使用 AsyncSifAPI，例如 'async with AsyncSifAPI(...) as api:'")

        url = f"{self.BASE_URL}/{endpoint}"
        params = self._build_params(country)

        # 构造最终 headers：合并基础 headers 并临时设置 referer
        headers = self.base_headers.copy()
        if referer:
            headers["Referer"] = referer

        async with self._session.post(
            url,
            params=params,
            json=data,
            headers=headers
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def fetch_asin_keyword_list(
        self,
        asin: str,
        time_piece_type: str = "latelyDay",
        time_piece_value: str = "7",
        page_num: int = 1,
        page_size: int = 10,
        sort_by: str = "scoreInfo.scoreRatio",
        desc: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        POST www.sif.com/api/search/asinKeywordList
        单行含 keyword、clickPurchaseRatio、cpc.legacyForSales_exact 等。
        若 data.total == 0，去掉 conditions 中的 isMultiVariantKw 后重试一次。
        """
        if not self._session:
            raise RuntimeError("请在异步上下文管理器中使用 AsyncSifAPI")

        effective_sort = (sort_by or "").strip() or "scoreInfo.scoreRatio"
        referer = (
            f"https://www.sif.com/reverse?country=US&from=commonAsinTab&asin={asin}"
            f"&isListingSearch=false&trafficType="
        )
        payload: Dict[str, Any] = {
            "pageSize": page_size,
            "pageNum": page_num,
            "desc": desc,
            "conditions": ["totalPeriod.total", "isMultiVariantKw"],
            "keyword": "",
            "asin": asin,
            "listingSearch": False,
            "timePieceType": time_piece_type,
            "timePieceValue": time_piece_value,
            "sortBy": effective_sort,
        }
        result = await self._post("search/asinKeywordList", payload, referer=referer)
        if result.get("code") != 1:
            raise Exception(f"API 错误: {result.get('msg', '未知错误')}")
        data_block = result.get("data") or {}
        total = data_block.get("total")
        list_data = data_block.get("list") or []
        if total == 0:
            payload_retry = {**payload, "conditions": ["totalPeriod.total"]}
            print(f"[SIF] asin={asin} total=0，去掉 isMultiVariantKw 后重试")
            result = await self._post("search/asinKeywordList", payload_retry, referer=referer)
            if result.get("code") != 1:
                raise Exception(f"API 错误(重试): {result.get('msg', '未知错误')}")
            data_block = result.get("data") or {}
            list_data = data_block.get("list") or []
        return list_data

    async def sif_asin(
        self,
        asin: str,
        time_piece_type: str = "latelyDay",
        time_piece_value: str = "7",
        page_num: int = 1,
        page_size: int = 10,
        sort_by: str = "scoreInfo.scoreRatio",
        desc: bool = True,
        need_wf: bool = False,
        conditions: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """获取指定 ASIN 的关键词行列表（asinKeywordList）。"""
        return await self.fetch_asin_keyword_list(
            asin=asin,
            time_piece_type=time_piece_type,
            time_piece_value=time_piece_value,
            page_num=page_num,
            page_size=page_size,
            sort_by=sort_by,
            desc=desc,
        )




    async def fetch_multiple_asins(
        self,
        asins: List[str],
        time_piece_type: str = "latelyDay",
        time_piece_value: str = "7",
        page_num: int = 1,
        page_size: int = 10,
        sort_by: str = "",
        desc: bool = True,
        need_wf: bool = False,
        conditions: Optional[List[str]] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        并发查询多个 ASIN 的关键词数据
        :return: 字典 {asin: keywords_list}
        """
        async def fetch_one(asin: str) -> tuple[str, List[Dict[str, Any]]]:
            # 使用信号量控制并发数
            async with self._semaphore:
                result = await self.sif_asin(
                    asin=asin,
                    time_piece_type=time_piece_type,
                    time_piece_value=time_piece_value,
                    page_num=page_num,
                    page_size=page_size,
                    sort_by=sort_by,
                    desc=desc,
                    need_wf=need_wf,
                    conditions=conditions
                )
                return asin, result

        tasks = [fetch_one(asin) for asin in asins]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理结果，异常时返回空列表并打印错误
        final_dict = {}
        for item in results:
            if isinstance(item, Exception):
                print(f"请求失败: {item}")
                continue
            asin, data = item
            final_dict[asin] = data
        return final_dict



# ==================== 使用示例 ====================
async def sif_main(asins: list[str]):
    # 优先从环境变量读取 JWT，避免把 token 写死在仓库里
    auth_token = (
        os.environ.get("SIF_AUTH_TOKEN", "").strip()
        or os.environ.get("SIF_TOKEN", "").strip()
        or "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ3ZWNoYXRpZCI6Im90SkwwNXc4MzRhenJ0Z3NRTVJDV0x5NmsxQjgiLCJ1c2VyU2FsdCI6InA4RHVzZVI2IiwiZXhwIjoxNzc3Nzc1MzE3LCJ1c2VyaWQiOiJqbXhOMTRiMW45NDMzM3BRNzAzSUFld3EiLCJwbGF0Zm9ybSI6Im9mZmljaWFsIn0.S8Lk-05BzftMesOjvpscuX93R9RUIMVO8KMZbH2NzYo"
    )
    cookies = {
        "Hm_lvt_8d71bef53342fdb284ff83594f3b97ff": "1773713262",
        "HMACCOUNT": "1B0FE40093B498DF",
        "sif_token": auth_token,
    }

    async with AsyncSifAPI(
        authorization_token=auth_token,
        cookies=cookies,
        max_concurrent=5,
    ) as api:
        multi_asin_results = await api.fetch_multiple_asins(asins)

        result: List[Dict[str, Any]] = []
        keyword_groups_dict: Dict[str, List[str]] = {}
        for asin, rows in multi_asin_results.items():
            # print(rows)
            try:
                if not rows:
                    print(f"{asin}: 0 个关键词")
                    keyword_groups_dict[asin] = []
                    result.append({asin: aggregate_sif_keyword_rows([], top_n=10)})
                    continue
                print(f"{asin}: {len(rows)} 个关键词")
                for row in rows[:10]:
                    kw = row.get("keyword")
                    if kw:
                        keyword_groups_dict.setdefault(asin, []).append(kw)
                agg = aggregate_sif_keyword_rows(rows, top_n=10)
                result.append({asin: agg})
                print({asin: agg})
            except Exception as e:
                print(f"{asin}: {e}")
        print("<准备关键词>", keyword_groups_dict)
        return keyword_groups_dict



if __name__ == "__main__":
    asins = ["B0GMV2D895","B0FWJ8HNCB"]
    print(asyncio.run(sif_main(asins)))