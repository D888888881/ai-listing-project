import base64
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

url = os.environ.get("NANO_BANANA_API_URL", "https://grsai.dakka.com.cn/v1/api/generate")


def _nano_banana_api_key() -> str:
    key = os.environ.get("NANO_BANANA_API_KEY", "sk-8a6dc9f2e6cf4c43907011d2215e3b52").strip()
    if not key:
        raise RuntimeError("请设置环境变量 NANO_BANANA_API_KEY")
    return key


def nano_banana_create_image(prompt, images, aspectRatio="1.1"):
    payload = json.dumps({
       "model": "nano-banana-2",
       "prompt": prompt,
       "images": images,
       "aspectRatio": aspectRatio,
       "imageSize": "2K",
       "replyType": "json"
    })
    headers = {
       "Authorization": f"Bearer {_nano_banana_api_key()}",
       "Content-Type": "application/json",
    }


    response = requests.request("POST", url, headers=headers, data=payload,timeout=130)

    print(response.text)
    return response.json()

def generate_image(index,images):
        try:
            result = nano_banana_create_image(prompt,images,"1:1")
            print(f"第 {index} 张图片生成成功")
            return {
                "index": index,
                "success": True,
                "result": result
            }

        except Exception as e:
            print(f"第 {index} 张图片生成失败: {e}")
            return {
                "index": index,
                "success": False,
                "error": str(e)
            }

# =========================
# 本地图片转 base64
# =========================
def image_to_base64(image_path):
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")


def image_to_data_uri(image_path, mime_type="image/jpeg"):
    b64 = image_to_base64(image_path)
    return f"data:{mime_type};base64,{b64}"

if __name__ == '__main__':
    prompt = '''
    ## 主图
recommended composition：正面微俯视15°，展示产品整体外观及功能细节
white background requirements：纯白底(RGB 255,255,255)，无阴影、无反光、无环境元素
product occupancy：产品需占据画面85%以上
must highlight features：材质纹理、按键区域、耳机功能标志、蓝色光效
【fabe analysis】
  feature：集成蓝牙耳机功能的眼罩，带有舒适材质和光效设计
  advantage：结合舒适性和科技感，提供多功能使用体验
  benefit：消费者可享受舒适佩戴同时听音乐或接听电话，提升睡眠质量和便捷性
special effects：悬浮效果和镜像倒影以增强科技感
notes：需确认光效设计是否为产品实际功能或仅为视觉效果
 
 备注：帮我把参考图里面的产品换成我上传的产品图，产品图以base64形式上传
    '''






    # start = time.time()
    # nano_banana_create_image(prompt, images, "1:1")
    # end = time.time()
    # print(f"总耗时: {end - start:.2f} 秒")

    # 同时生成3张图片
    results = []
    start = time.time()
    images = [
        "https://m.media-amazon.com/images/I/81pNa09M4nL._AC_SL1500_.jpg",
        "https://m.media-amazon.com/images/I/71xECxu1YGL._AC_SL1500_.jpg",
        "https://m.media-amazon.com/images/I/71iaGdushTL._AC_SL1500_.jpg",
        "https://m.media-amazon.com/images/I/714AjeAo7QL._AC_SL1500_.jpg",
        "https://m.media-amazon.com/images/I/71JFAeFRgYL._AC_SL1500_.jpg",
        "https://m.media-amazon.com/images/I/717LFolEP-L._AC_SL1500_.jpg",
        image_to_data_uri("5370c3390acb.jpg")

    ]

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(generate_image, i,images)
            for i in range(1, 2)
        ]

        for future in as_completed(futures):
            results.append(future.result())
    end = time.time()
    print(f"总耗时: {end - start:.2f} 秒")
    print("全部任务完成")
    print(results)