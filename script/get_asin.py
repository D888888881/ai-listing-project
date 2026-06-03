import re
import requests


def get_asins(keyword: str, *, verbose: bool = False) -> list[str]:
    headers = {
        "Referer": "https://www.amazon.com/s?k=manta+sound&crid=23MJH0IB3SGQ&sprefix=manta+sound%2Caps%2C309&ref=nb_sb_noss_1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
        "device-memory": "32",
        "downlink": "1.55",
        "dpr": "1.5",
        "ect": "3g",
        "rtt": "300",
        "sec-ch-device-memory": "32",
        "sec-ch-dpr": "1.5",
        "sec-ch-ua": "\"Microsoft Edge\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
        "sec-ch-ua-full-version-list": "\"Microsoft Edge\";v=\"147.0.3912.72\", \"Not.A/Brand\";v=\"8.0.0.0\", \"Chromium\";v=\"147.0.7727.102\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-ch-ua-platform-version": "\"19.0.0\"",
        "sec-ch-viewport-height": "712",
        "sec-ch-viewport-width": "2552",
        "viewport-width": "2552"
    }
    url = "https://www.amazon.com/s"
    params = {
        "crid": "LCZEBO653FTY",
        "i": "aps",
        "k": keyword,
        "ref": "nb_sb_noss_1",
        "sprefix": f"{keyword},aps,378",
        "url": "search-alias=aps"
    }
    response = requests.get(url, headers=headers, params=params)
    print(response.text)
    if verbose:
        print(response)
    html = response.text
    asin_list = re.findall(r'data-asin="([^"]+)"', html)

    # 去重（过滤掉可能的空字符串）
    seen = set()
    unique_asins = []
    for asin in asin_list:
        if asin and asin not in seen:      # 跳过空值并去重
            seen.add(asin)
            unique_asins.append(asin)

    # 或者一行代码（无序但更简洁）：
    # unique_asins = list(set(a for a in asin_list if a))

    if verbose:
        print(f"共提取到 {len(unique_asins)} 个唯一 ASIN：")
        for asin in unique_asins:
            print(asin)
    return unique_asins[:20]

if __name__ == "__main__":
    get_asins("kumfi lumbar support pillow", verbose=True)