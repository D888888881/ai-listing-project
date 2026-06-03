from openai import OpenAI
import os

API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai-proxy.org/v1")
MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")
if not API_KEY:
    raise RuntimeError("请设置环境变量 OPENAI_API_KEY")

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

# ========== 你要分析的图片（替换为真实链接） ==========
images =  [
        "https://m.media-amazon.com/images/I/71PVF5dICKL._AC_SL1500_.jpg",
        "https://m.media-amazon.com/images/I/71Mt6RBse0L._AC_SL1500_.jpg",
        "https://m.media-amazon.com/images/I/819HrBg07WL._AC_SL1500_.jpg",
        "https://m.media-amazon.com/images/I/71iBYfSLd2L._AC_SL1500_.jpg",
        "https://m.media-amazon.com/images/I/81HbFcedIdL._AC_SL1500_.jpg",
        "https://m.media-amazon.com/images/I/71QcKt6kU8L._AC_SL1500_.jpg"
      ]
# images =  [
#         "https://m.media-amazon.com/images/I/811N1zMrUBL._AC_SL1500_.jpg",
#         "https://m.media-amazon.com/images/I/8171138yHYL._AC_SL1500_.jpg",
#         "https://m.media-amazon.com/images/I/71dQf1KpdiL._AC_SL1500_.jpg",
#         "https://m.media-amazon.com/images/I/71TFinPcNYL._AC_SL1500_.jpg",
#         "https://m.media-amazon.com/images/I/71W-7yoIYLL._AC_SL1500_.jpg",
#         "https://m.media-amazon.com/images/I/71cg44QkSAL._AC_SL1500_.jpg"
#       ]
# 可继续添加更多副图

# ========== System Prompt ==========
system_prompt = """
你是一位资深亚马逊视觉设计师和A+内容策划专家，精通平台主图、副图的设计规范与转化逻辑。

用户会提供一套产品的多角度图片（含主图和副图），请你基于图片分析，生成一份专业的，详细的“亚马逊主图与副图设计需求最终报告”。
主图要在商品上挂一个吊牌，吊牌突出展示商品的主要卖点，字体要显眼，让用户一眼就能看见
必须遵守一个字体原则，一套主图-副图的字体必须一致，不能出现一套主图-副图图片有几种不一样的字体
有些图片会有一些功能标注，例如标注尺寸，或者标注某个功能细节Thickness Adjuster Not for Cutting,请你也要分析这些标注信息，并结合图片内容，给出设计建议，一张图片最多两个标注。 
要严格按照FABE法则，从Feature（功能特征）、Advantage（功能优势）、Benefit（消费者利益）三个维度分析每一张图片的设计需求，并给出具体的设计建议。
输出必须严格遵循以下结构，语言具体、可执行，不使用模糊描述。
输出必须是一个合法的 JSON 对象，不包含任何额外文本、注释或 Markdown 代码块标记。JSON 结构严格定义如下，所有字段不可缺失，如果某部分信息不足请基于合理推测填写并注明“需确认”。

{
  "report": {
    "main_image": {
      "recommended_composition": "根据产品造型给出最佳角度（如正面微俯视15°），并说明理由",
      "white_background_requirements": "纯白底(RGB 255,255,255)，无阴影、无反光、无环境元素",
      "product_occupancy": "产品需占据画面85%以上",
      "must_highlight_features": "从图中识别出必须清晰展示的细节（如材质纹理、接口、按键、logo位置等）",
      "fabe_analysis": {
        "feature": "从主图中提炼的核心功能特征",
        "advantage": "该特征相比竞品或旧款的优势",
        "benefit": "该特征为消费者带来的直接利益"
      },
      "notes": "需确认的信息或基于推测的说明"
    },
    "image_2_core_selling_point": {
      "recommended_composition": "产品在使用场景中的展示角度",
      "scene_suggestion": "基于产品用途推荐的生活化场景（描述背景、光线、氛围）",
      "selling_point_display": "如何通过画面表现1个最重要功能（如'刀片锋利'用切割食材的动态瞬间呈现）",
      "text_overlay": {
        "copy": "建议添加的简短卖点文字（英文≤5词）",
        "position": "文字位置",
        "annotations": [
          {
            "label": "标注1文字",
            "description": "标注所强调的功能细节或优势"
          },
          {
            "label": "标注2文字（若只有1个标注，第二个填空字符串）",
            "description": "标注所强调的功能细节或优势"
          }
        ]
      },
      "fabe_analysis": {
        "feature": "该场景突出的功能特征",
        "advantage": "功能优势",
        "benefit": "消费者利益"
      },
      "notes": "需确认的信息"
    },
    "image_3_size_comparison": {
      "composition": "产品与常见参照物（如硬币、手掌、手机）的对比摆放方式",
      "annotation_details": {
        "dimensions": "根据图片估算或推断的关键尺寸（长/宽/高）",
        "reference_object": "所选的参照物",
        "line_style": "标注线的风格建议（简洁线条+数字）"
      },
      "background": "建议浅色渐变或纯灰背景，保持专业感",
      "fabe_analysis": {
        "feature": "尺寸相关的特征",
        "advantage": "尺寸带来的优势（如便携、大容量）",
        "benefit": "消费者利益"
      },
      "notes": "需确认的信息"
    },
    "image_4_detail_closeup": {
      "closeup_parts": [
        {
          "part_name": "需放大的部位1",
          "method": "特写手法（微距、局部放大标注、局部彩色保留等）",
          "annotation_text": "用引线+简短文字说明该细节的功能优势"
        },
        {
          "part_name": "需放大的部位2",
          "method": "特写手法",
          "annotation_text": "功能优势说明"
        },
        {
          "part_name": "需放大的部位3（若不足3处可填无）",
          "method": "特写手法",
          "annotation_text": "功能优势说明"
        }
      ],
      "overall_annotation_style": "细引线+简短文字的风格描述",
      "fabe_analysis": {
        "feature": "这些细节整体体现的功能特征",
        "advantage": "综合优势",
        "benefit": "消费者利益"
      },
      "notes": "需确认的信息"
    },
    "image_5_usage_steps": {
      "display_method": "序列图（2-3步）或一张图内分区展示",
      "steps": [
        {
          "step_number": 1,
          "description": "第一步画面内容（手部动作、产品状态）"
        },
        {
          "step_number": 2,
          "description": "第二步画面内容"
        },
        {
          "step_number": 3,
          "description": "第三步画面内容（若只有两步，则填无）"
        }
      ],
      "auxiliary_elements": "箭头、编号、步骤标题等辅助元素描述",
      "fabe_analysis": {
        "feature": "使用流程相关的功能特征",
        "advantage": "操作便利性优势",
        "benefit": "消费者利益"
      },
      "notes": "需确认的信息"
    },
    "image_6_package_contents": {
      "items": ["主机", "充电线", "说明书", "其他根据图片推测的物品"],
      "layout": "扁平排列或立体陈列，要求干净整洁",
      "notes": "待客户确认的项目或无法从图中确认的物品"
    },
    "image_7_function_breakdown": {
      "display_method": "将产品分解成可视化模块或功能区块",
      "composition_angle": "半俯视或透视角，保证每个模块清楚可辨",
      "modules": [
        {
          "number": 1,
          "name": "模块名称",
          "function_description": "简短功能说明"
        },
        {
          "number": 2,
          "name": "模块名称",
          "function_description": "简短功能说明"
        }
      ],
      "annotation_style": "编号 + 功能名称或简短说明",
      "background": "浅色渐变或纯白",
      "fabe_analysis": {
        "feature": "模块化设计体现的功能特征",
        "advantage": "结构优势",
        "benefit": "消费者利益"
      },
      "notes": "需确认的信息"
    },
    "image_8_extended_scene": {
      "scene_description": "产品在特定场景中的延伸使用（如户外、餐桌、办公室等）",
      "composition_angle": "侧面或45°角，突出产品在场景中的实际效果",
      "lighting_atmosphere": "柔和自然光或暖色调，制造温馨或高端感",
      "text_overlay": {
        "copy": "简短功能或卖点文字 ≤5词",
        "position": "建议右下角"
      },
      "fabe_analysis": {
        "feature": "场景扩展体现的额外功能",
        "advantage": "多功能性或附加值优势",
        "benefit": "消费者利益"
      },
      "notes": "需确认的信息"
    }
  }
}

请现在开始分析我上传的产品图片，并按上述 JSON 结构输出设计需求报告。只输出纯 JSON，不要包含任何其他内容。
"""

# ========== 构造 User Message（多图+指令） ==========
user_content = [
    {"type": "text", "text": "请详细分析以下商品图片的每一处细节，按系统指令生成图需简报。"},
    # 继续添加更多图片...
]

for image in images:
    user_content.append({"type": "image_url", "image_url": {"url": image}})

print(user_content)
# ========== 调用 ==========
response = client.chat.completions.create(
    model=MODEL,
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ],
    max_tokens=3500,
    temperature=0.3   # 低温度保证输出稳定专业
)

# 打印生成的图需文档
print(response.choices[0].message.content)