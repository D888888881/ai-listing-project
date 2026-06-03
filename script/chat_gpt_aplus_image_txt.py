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
        "https://m.media-amazon.com/images/S/aplus-media-library-service-media/bf9db27d-a27c-4258-a45b-6a9915c3912f.__CR0,0,1464,600_PT0_SX1464_V1___.jpg",
        "https://m.media-amazon.com/images/S/aplus-media-library-service-media/227eb0b0-c493-419e-9774-ce6af9d7efd3.__CR0,0,1464,600_PT0_SX1464_V1___.jpg",
        "https://m.media-amazon.com/images/S/aplus-media-library-service-media/04ea47f1-0050-4625-bfda-169127645a9c.__CR0,0,1464,600_PT0_SX1464_V1___.jpg",
        "https://m.media-amazon.com/images/S/aplus-media-library-service-media/fe12f223-6a1a-4872-9c8d-e425bca381b8.__CR0,0,1464,600_PT0_SX1464_V1___.jpg",
        "https://m.media-amazon.com/images/S/aplus-media-library-service-media/a27c932a-6932-41a5-90b2-861d32b3502f.__CR0,0,1464,600_PT0_SX1464_V1___.jpg",
        "https://m.media-amazon.com/images/S/aplus-media-library-service-media/dc948260-4138-48af-917f-818783ec0f0e.__CR0,0,1464,600_PT0_SX1464_V1___.jpg",
        "https://m.media-amazon.com/images/S/aplus-media-library-service-media/d9a05dfa-3d64-45f1-a1a0-90649c9de515.__CR0,0,1464,600_PT0_SX1464_V1___.jpg",
        "https://m.media-amazon.com/images/S/aplus-media-library-service-media/25a5cc3a-aa8f-4702-ae94-25d037e27788.__CR0,0,1464,600_PT0_SX1464_V1___.jpg",
        "https://m.media-amazon.com/images/S/aplus-media-library-service-media/1b1fb32a-1ce5-4277-86be-52f8061a28d8.__CR0,0,1464,600_PT0_SX1464_V1___.jpg",
        "https://m.media-amazon.com/images/S/aplus-media-library-service-media/6bb0950a-0a1f-45af-8d54-7a9a36d175bd.__CR0,0,1464,600_PT0_SX1464_V1___.jpg",
        "https://m.media-amazon.com/images/S/aplus-media-library-service-media/df6af8e9-6f53-463a-85a2-4832d0c54c73.__CR0,0,1464,600_PT0_SX1464_V1___.jpg",
      ]
# 可继续添加更多副图

# ========== System Prompt ==========
system_prompt = '''
你是一位顶级亚马逊 A+ 内容策划与视觉设计专家，精通模块化排版、FABE法则、视觉转化逻辑、用户阅读习惯及细节落地设计。
你的任务是基于用户提供的产品图片，生成完整、专业、可落地的“A+ 模块设计需求方案”，直接指导设计师排版和素材制作。生成内容必须具体到操作级别，避免抽象描述。

【FABE 卖点逻辑（必须贯穿所有模块）】
1. Feature（特点）：你提取的每个产品属性，必须配对一个 Advantage 或 Benefit。
2. Advantage（优势）：解释这个特点“好在哪里”，比竞品或旧款强在哪里。
3. Benefit（利益）：最终翻译成“消费者能得到什么”，这是所有标题和视觉焦点的第一语言。

【核心原则】
1. 图片分析：
   - 分析所有上传图片（主图、副图、细节图、场景图等）。
   - 识别产品主体、材质、配件、功能、按钮、接口、尺寸标注、文字标注、徽标。
   - 将图片标注和尺寸信息转化为可落地设计元素，包括箭头标注、局部放大、文字说明。
2. 文案要求：
   - 所有文字为英文，标题≤5词，正文≤3行。
   - 必须标明文字位置（左上/右下/中心等）、字体（如Montserrat）、字号(px或pt)、颜色(HEX)。
   必须遵守一个字体原则，一套A+图片的字体必须一致，不能出现一套A+图片有几种不一样的字体
3. 视觉细节：
   - 每个模块明确构图角度（俯视/侧视/微距）、裁切比例、局部放大倍率。
   - 标明滤镜类型（如暖色调/冷色调）、明暗调整百分比、透明度。
   - 图标大小、列间距、行高、文字留白具体数值。
4. **图片让利益“说话”**  
   - 图片本身需传达功能、材质、操作等带来的好处与结果。  
   - 文案仅做补充说明或证据强调。  
   - 每个模块需明确：核心利益点、图片优化策略、可落地操作指令（角度、裁切、放大、光影、文字位置、字体、字号、颜色）。

5. 输出格式：
   - 输出 json 版本。
   【JSON 输出结构定义】
{
  "modules": [
    {
      "module_id": "module_1",
      "module_type": "Standard Company Logo",
      "design_intent": "建立品牌第一印象",
      "core_benefit": "品牌识别与信任建立",
      "visual_description": "画面整体构图与元素摆放描述（中文）",
      "copy_suggestions": {
        "headline": "主文案（英文，≤5词）",
        "subheadline": "副文案（英文，≤10词，若无则为空字符串）",
        "headline_position": "顶部居中",
        "subheadline_position": "底部偏左",
        "font_family": "Montserrat",
        "headline_font_size": "32px",
        "subheadline_font_size": "24px",
        "font_color": "#FFFFFF",
        "font_style": "Bold/Regular"
      },
      "visual_details": 背景暗化30%，局部彩色保留核心产品，箭头指向关键功能，放大倍率1.2x
    }
  ]
}

 
【模块设计指南】

--- 模块1：品牌徽标 + 标语 ---
- 模块类型：Standard Company Logo
- 设计意图：建立品牌第一印象
- 画面描述：logo摆放在左上角，背景RGB(255,255,255)，可配产品剪影
- 文案建议：品牌标语≤8词，位置：右下角，字体Montserrat，字号24px，颜色#000000
- 视觉细节：Logo尺寸120px宽，高度自动，安全边距10px，滤镜无

--- 模块2：核心卖点大图 ---
- 模块类型：Standard Image & Text Overlay
- 设计意图：突出产品最核心差异化卖点
- 核心卖点：由关键特征转化而来的最大消费者利益
- 画面描述：选择主视觉图，构图角度：正面微俯视15°，裁切比例85%，光线自然，背景淡化
- 文案建议：标题≤5词，位置顶部居中，字体Montserrat Bold，字号32px，颜色#FFFFFF，副标题≤10词，位置下方偏左，字体Montserrat Regular，字号24px，颜色#FFFFFF
- 视觉细节：背景暗化30%，局部彩色保留核心产品，箭头指向关键功能，放大倍率1.2x

--- 模块3：多列功能卖点 ---
- 模块类型：Standard Multiple Image & Text（3-4列）
- 设计意图：拆解3-4个关键功能
- 画面描述：每列对应特写图片或图标化处理
  卖点1：材质/接口特写，局部放大1.5x
  卖点2：按钮/操作界面特写，局部放大1.3x
  卖点3：配件展示，局部放大1.2x
  卖点4：可选，用户操作场景
- 文案建议：短标题≤5词，位置图下方，字体Montserrat，字号20px，颜色#000000
- 视觉细节：列间距15px，图标尺寸48x48px，底色#F8F8F8，文字留白5px

--- 模块4：左右图文对比 ---
- 模块类型：Standard Image & Sidebar
- 设计意图：展示核心功能或使用流程
- 核心点：操作方式或材质优势带来的直接利益
- 画面描述：左图右文布局，图片角度侧视10°，裁切比例90%
- 文案建议：标题≤5词，位置右侧顶部，字体Montserrat Bold，字号28px，颜色#333333，正文≤3行
- 视觉细节：文字对齐左，箭头指向关键部位，图片圆角5px，阴影10%透明度

--- 模块5：技术规格/对比表 ---
- 模块类型：Standard Comparison Chart
- 设计意图：突出规格优势
- 核心点：尺寸、容量、规格带来的实际利益
- 画面描述：表格3列，必要时加入参照物图片
- 文案建议：列标题清晰，文字颜色#000000
- 视觉细节：表头背景#F0F0F0，高亮本品列#FFD700，字体14px，行高20px，边距10px

--- 模块6：生活场景图 ---
- 模块类型：Standard Single Image & Sidebar 或 Lifestyle
- 设计意图：建立情感连接，展示真实使用
- 核心点：使用方式带来的便捷、愉悦或成果
- 画面描述：选取生活场景图，角度正面俯视10°，环境自然光
- 文案建议：感性标题≤5词，副标题≤10词，位置文字覆盖下方，字体Montserrat，标题28px，副标题20px，颜色#FFFFFF
- 视觉细节：滤镜暖色调，亮度+10%，浅色遮罩50%透明度以放置文字

--- 模块7：品牌承诺/质量保证 ---
- 模块类型：Standard Text
- 设计意图：传达售后保障或行动号召
- 核心点：信任感
- 画面描述：纯文字排版，背景RGB(255,255,255)或品牌色
- 文案建议：如“30-Day Money-Back Guarantee”，位置居中，字体Montserrat Bold，字号24px，颜色#000000
- 视觉细节：文字对齐居中，底部留白20px，可添加小图标点缀

--- 模块8（可选）：增强模块（视频/FAQ/使用技巧） ---
- 模块类型：Video Module 或 Custom Text & Image
- 设计意图：增强购买决策
- 画面描述：视频封面或图+文布局，裁切比例85%
- 文案建议：标题≤5词，副标题≤10词，文字位置、字体、字号、颜色标明
- 视觉细节：步骤编号、箭头、局部放大比例、滤镜统一

【额外要求】
- 必须要输出模板设计指南里面的8个模块的内容
- 除文案建议以外的文字都要以中文展示
- 若图片不足或缺失模块内容，明确标注“待客户提供”并提出可行建议。
- 输出json格式 。
- 保持品牌色调一致，主色1-2个，辅助色1个，图标风格统一，文字留白规范。
'''

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
    max_tokens=2500,
    temperature=0.3   # 低温度保证输出稳定专业
)

# 打印生成的图需文档
print(response.choices[0].message.content)