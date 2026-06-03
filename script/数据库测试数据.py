import os
import django

# 请替换为你的实际项目名
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ai_listing_project.settings')
django.setup()

# 请替换为你的实际 app 名
from myapp.models import AsinAnalysis, AnalysisDetail

def insert_data():
    # 如果已存在该 ASIN，先删除（避免重复）
    AsinAnalysis.objects.filter(asin='B01B8R6PF2').delete()


    listing_txt = """
    
    Title 标题

AA Batteries 100 Pack, 1.5V Alkaline Double A Batteries for Remote Controls, Toys, Flashlights, Clocks, Game Controllers & Household Devices, Long Shelf Life Battery Value Pack for Home & Emergency BackupAA 电池 100 包，1.5V 碱性双 A 电池，适用于遥控器、玩具、手电筒、闹钟、游戏控制器和家用电器，长寿命电池，家庭和应急备用电池组合装

Bullet Points 五点描述
1. Everyday AA Power for Home Devices1. 日常 AA 电池，为家用设备供电

Keep reliable power ready for remote controls, TV remotes, clocks, toys, wireless mouse, flashlights, game controllers, holiday décor, and other everyday household devices. These aa batteries are made for daily replacement needs at home, in storage, or on the go.为遥控器、电视遥控器、时钟、玩具、无线鼠标、手电筒、游戏控制器、节日装饰和其他日常家用设备提供可靠的电源。这些 AA 电池专为家庭日常更换需求、存储或外出使用而设计。

2. Great Value Double A Batteries Pack2. 大品牌双 A 电池套装

This batteries aa size pack gives you a practical supply for families, homeowners, gamers, and anyone who frequently replaces drained batteries. Store extras in a drawer, garage shelf, utility closet, or emergency kit so backup power is always within reach.这款 AA 电池套装为家庭、房主、游戏玩家以及经常更换耗尽电池的任何人提供了实用的电源供应。将备用电池存放在抽屉、车库架子、杂物间或应急包中，以便随时取用备用电源。

3. Reliable 1.5V Alkaline Performance3. 可靠 1.5V 碱性性能

Each double a battery delivers dependable 1.5V alkaline power for low-to-medium drain devices such as remote controls, clocks, toys, flashlights, and wireless accessories. Designed for consistent everyday use and convenient household backup.每节双 A 电池提供可靠的 1.5V 碱性电力，适用于低至中等耗电设备，如遥控器、时钟、玩具、手电筒和无线配件。专为日常持续使用和便捷的家庭备用电源设计。

4. Ready for Seasonal & Emergency Use4. 随时准备用于季节性和紧急情况

Ideal for Christmas decorations, LED candles, flashlights, battery-powered lights, garage storage, travel bags, and car emergency kits. Keep batteries ready for seasonal activities, power outages, storms, camping, and unexpected device needs.适合圣诞装饰，LED 蜡烛，手电筒，电池供电的灯具，车库储物，旅行包和汽车应急套件。为季节性活动、停电、风暴、露营和意外设备需求备好电池。

5. Easy to Store, Single-Use Batteries5. 易于存放，一次性电池

The organized value pack helps keep extra aa batteries together for fast access and simple storage. These are single-use alkaline batteries and are not rechargeable. Store in a cool, dry place and remove from devices during long periods of non-use.组织好的价值包有助于将额外的 AA 电池放在一起，以便快速访问和简单存储。这些是一次性碱性电池，不可充电。存放在凉爽、干燥的地方，并在长时间不使用时从设备中取出。

Product Description 长描述产品描述 长描述

Power the everyday devices your home depends on.为您家中依赖的日常设备提供动力。

This AA batteries value pack is designed for daily household use, backup storage, seasonal décor, toys, remote controls, game controllers, flashlights, clocks, wireless accessories, and emergency kits. Whether you are replacing drained batteries, preparing for holidays, stocking a garage shelf, or keeping backup power in your car trunk, this double a batteries pack helps make sure you always have fresh power ready.这款 AA 电池套装专为日常家庭使用、备用存储、季节性装饰、玩具、遥控器、游戏控制器、手电筒、时钟、无线配件和应急套件而设计。无论您是更换耗尽的电池、为假期做准备、在车库货架上备货，还是在汽车后备箱中保持备用电源，这款双 AA 电池套装都能确保您始终有新鲜电源可用。

Perfect for homeowners, parents, gamers, and families, these 1.5V alkaline AA batteries are a practical choice for common low-to-medium drain devices around the home. Use them for TV remotes, streaming remotes, children’s toys, Xbox-style controllers, LED candles, flashlights, clocks, wireless mouse devices, and more.

These are single-use alkaline batteries and are not rechargeable. For best results, store batteries in a cool, dry place and remove them from devices that will not be used for a long time.这些是一次性碱性电池，不可充电。为了获得最佳效果，请将电池存放在凉爽干燥的地方，并在长时间不使用设备时取出电池。

Backend Search Terms 后台关键词

aa batteries batteries double a batteries batteries aa size pack batteries aa alkaline aa batteries 1.5v aa battery pack double a battery pack remote control batteries toy batteries flashlight batteries game controller batteries household batteries emergency batteries aa size batteries bulk aa batteries battery value packaa 电池 电池 双 a 电池 电池 aa 尺寸电池包 aa 碱性电池 aa 电池 1.5v aa 电池包 双 a 电池包 遥控器电池 玩具电池 手电筒电池 游戏手柄电池 家用电池 应急电池 aa 尺寸电池 大容量 aa 电池 电池套装
    
    """

    asin_obj = AsinAnalysis.objects.create(
        asin='B01B8R6PF2',
        listing=listing_txt.strip()
    )

    # ========== 1. refus（Rufus 买家问题分析）==========
    refus_text = """
一、Ask Rufus 买家问题分析
Rufus 问题 1：Can these batteries be used in remote controls?Rufus 问题 1：这些电池可以用在遥控器上吗？

这是最直接的转化问题。VOC 里“为遥控器供电”占 19.5%，说明遥控器是核心使用场景之一。

建议：
Listing 标题、五点、图片文案都要明确写：
for remote controls, toys, flashlights, clocks, game controllers, wireless mouse, household devices对于遥控器、玩具、手电筒、时钟、游戏控制器、电线等标题、五点、图片文案都要明确写：适用于遥控器、玩具、手电筒、时钟、游戏控制器、无线鼠标、家用设备

不要只写 “wide range of devices”，要把买家最常问的设备列出来。

Rufus 问题 2：Do they work well in cold temperatures?Rufus 问题 2：它们在低温下工作得好吗？

这类问题背后是：买家想把电池放在车库、储物区、汽车后备箱、户外设备、应急手电筒里。VOC 里使用地点显示：车库或储物区占 18%，汽车后备箱占 12%，应急准备占 15%。

建议：
如果你的产品没有专业低温测试，不要写 “works great in extreme cold”。
建议写得更稳妥：

Reliable everyday power for indoor devices, storage areas, flashlights, and emergency backup kits.室内设备、存储区域、手电筒和应急备份套件的可靠日常电源。

如果产品有低温测试报告，可以升级成：

Built for dependable performance in common household and seasonal storage conditions.为在常见的家庭和季节性存储条件下提供可靠的性能而设计。

Rufus 问题 3：Are they made with recycled materials?Rufus 问题 3：它们是由回收材料制成的吗？

这是环保与材料信任问题。但如果你没有真实认证，不建议写 recycled materials。竞品页面主推的是 1.5V、100-pack、10-year shelf life、设备兼容和易开包装，并没有把 recycled material 作为主卖点。

建议：

不要虚假写：

Made with recycled materials由回收材料制成
Eco-friendly battery环保电池
Green battery绿色电池
Sustainable battery可持续电池

除非你有认证。可以安全表达为：

Easy-to-store value pack易于储存的套装
Organized packaging for home storage用于家庭存储的有序包装
Designed for long-term storage and everyday use设计用于长期存储和不断
Rufus 问题 4：Why you might like this

Rufus 会自动总结“为什么值得买”。你要让它抓到这些关键词：

AA batteriesAA 电池
double a batteries双倍 AA 电池
batteries aa size packAA 电池套装
long shelf life长效电池
remote controls遥控器
toys玩具
flashlights手电筒
game controllers游戏控制器
emergency backup紧急备用
value pack价值包

建议：
页面主线不要只讲便宜，要讲：

A practical AA battery value pack for everyday household devices, seasonal décor, gaming controllers, toys, flashlights, and emergency backup.一款实用的 AA 电池套装，适用于日常家用电器、季节性装饰品、游戏手柄、玩具、手电筒和应急备用。

Rufus 问题 5：Compare with similar鲁弗斯问题 5：与类似产品比较

买家会比较：容量、数量、保质期、漏液风险、设备兼容、价格。

建议：
A+ 或副图做对比表：

对比项	Our AA Batteries我们的 AA 电池	Ordinary AA Batteries普通 AA 电池
Device Use设备使用	Remote controls, toys, flashlights, controllers遥控器，玩具，手电筒，控制器	Basic household use基本家用
Storage存储	Easy-to-store pack易于	Loose or hard to organize松动或难以整理
Backup Use备份用途	Good for emergency kits适合应急包	Not always storage-focused不总是专注于存储
Value价值	Bulk pack for frequent replacement大包装，适合频繁更换	Smaller pack, higher unit cost小包装，单价更高
Trust Point信赖	Fresh power, reliable 1.5V output新鲜电力，可靠输出 1.5V	Inconsistent quality risk质量不一致风险
"""

    # ========== 2. negative（差评与未被满足需求分析）==========
    negative_text = """
二、差评与未被满足需求分析

VOC 差评集中在 5 个问题。虽然这部分没有百分比，所以不能强行编占比，但可以按转化风险排序。

1. 电池续航时间短

差评提到：电池无法长时间保持电量，甚至部分到货时没电。

建议：

Listing 要强调：

fresh power新鲜力量
long-lasting performance持久性能
reliable 1.5V output可靠 1.5V 输出
great for low-to-medium drain devices适用于低至中等耗电设备

但不要夸大成：

longest lasting最持久
lasts forever永不耗尽
best battery最好的电池
guaranteed all devices保证所有设备
2. 质量低劣 / 提前失效

VOC 里提到质量控制、部分电池不能用、提前停止工作。

建议：

页面要强化：

quality checked质量检查
ready to use随时可用
dependable power可靠电源
designed for daily household devices专为日常家用电器设计

如果你有质检流程，副图可以做：

Each Pack Quality Checked Before Shipping每包出厂前均经过质量检查

3. 充电问题

这里要特别注意：你给的竞品是 AA alkaline batteries，属于一次性碱性电池，不是 rechargeable batteries。竞品页面也强调 1.5V AA alkaline batteries。

建议：

如果你的产品也是碱性电池，Listing 里必须明确：

Single-use alkaline batteries. Not rechargeable.一次性碱性电池。不可充电。

这样可以减少因误解产生的差评。

4. 电池漏液

VOC 差评里有“电池漏液”，这是电池类产品最影响信任的痛点。

建议：

如果产品有防漏设计或保质期承诺，可以写：

Leak-resistant design for safe storage防漏设计，安全存储

如果没有测试，不要写 “leak-proof”。
可以保守写：

Store in a cool, dry place and remove from devices when not in use for long periods.存放在凉爽干燥的地方，长时间不使用时从设备中取出。

5. 包装设计不佳

未满足需求中提到希望改进包装，避免运输中损坏。

建议：

副图加入：

organized battery tray有组织的电池托盘
easy-open box易开盒
easy to store extras易于存放额外物品
keep batteries together for home, garage, or emergency kits将电池放在一起，用于家庭、车库或应急套件
"""

    # ========== 3. voc（人群特征、使用时间、场景、购买动机分析）==========
    voc_text = """
三、人群特征、使用时间、场景、购买动机分析

以下严格按照 VOC 里已有百分比来分析。

1. 性别画像
性别	占比	Listing 策略
女性	52%	页面风格要偏家庭、收纳、儿童玩具、遥控器、节日装饰
男性	48%	同时保留游戏手柄、手电筒、车库、应急备用场景

总结：
这不是明显偏男或偏女的产品，Listing 要做成家庭通用型电池补给包，不要做成纯工具类或纯母婴类。

2. 人群特征
人群	占比	需求理解
房主	25%	家里遥控器、钟表、温控器、手电筒、备用电池需求大
家长	20%	儿童玩具、婴儿音乐播放器、节日装饰是重点场景
游戏玩家	15%	Xbox 手柄、游戏设备需要稳定供电

总结：
前三类人群加起来占 60%。所以 Listing 的场景优先级应该是：

家庭日常 > 儿童玩具 > 游戏设备 > 应急备用 > 节日装饰

3. 使用时刻
使用时刻	占比	Listing 表达
日常使用	30%	everyday household power日常家用电源
季节性活动	18%	holiday decorations, Christmas lights, seasonal décor节日装饰，圣诞灯饰，季节性装饰
应急准备	15%	flashlights, emergency kits, backup power手电筒，应急包，备用电源

总结：
日常使用是第一需求，占 30%。所以标题和主图不要过度强调“应急”，而是先讲 everyday AA batteries for household devices。

4. 使用地点
使用地点	占比	Listing 表达
家中	35%	remote controls, clocks, toys, wireless mouse遥控器、时钟、玩具、无线鼠标
车库或储物区	18%	storage box, backup shelf, garage devices储物箱，备份架，车库设备
汽车后备箱	12%	emergency flashlight, roadside backup kit应急手电筒，路边备份套件

总结：
家中使用占 35%，是最大地点场景。副图第一张建议做“家庭设备合集”，第二张再做“车库/应急备用”。

5. 行为数据
行为	占比	转化启发
更换已耗尽的电池	28%	买家需要大包装、随手可取、不断电
为遥控器供电	19.5%	Rufus 问题必须直接回答 remote controls
作为手电筒和设备的应急备用电源	14%	要做 emergency backup 场景图

总结：
买家不是为了“尝鲜”购买，而是为了补库存、替换没电电池、家里随时有备用。
"""

    # ========== 4. suggestions（五条差异化建议）==========
    suggestions_text = """
四、五条差异化建议
1. 差异化方向：从“便宜大包装”升级为“家庭电池补给包”

不要只说 value pack。
更好的定位是：

AA Battery Value Pack for Everyday Home, Toys, Remotes, Gaming & Emergency BackupAA 电池通用套装，适用于日常家居、玩具、遥控器、游戏和应急备用

这样能同时覆盖房主 25%、家长 20%、游戏玩家 15%。

2. 强化 Remote Control 场景，直接承接 Rufus 问题

因为“为遥控器供电”占 19.5%，而 Rufus 第一个问题也是 remote controls。

建议五点第一条写：

Perfect for remote controls, TV remotes, streaming remotes, clocks, toys, flashlights, wireless mouse, and everyday household devices.适用于遥控器、电视遥控器、流媒体遥控器、时钟、玩具、手电筒、无线鼠标和日常家用电器。

3. 应急备用不要放第一，但必须做成强副卖点

应急准备占 15%，手电筒和设备备用电源占 14%，汽车后备箱占 12%。

建议副图文案：

Keep Backup Power Ready for Flashlights, Garage Storage & Emergency Kits备用电源，为手电筒、车库存储和应急包供电

4. 避免“可充电”误导，降低差评风险

VOC 差评里有“充电问题”，但你的关键词是 aa batteries，竞品是 alkaline batteries，不是 rechargeable batteries。

建议明确写：

Single-use alkaline batteries. Not rechargeable.一次性碱性电池。不可充电。

这条非常重要，可以减少误购差评。

5. 做“场景矩阵图”，而不是只拍电池

建议副图按占比排序：

家中设备：35%
日常使用：30%
替换耗尽电池：28%
遥控器：19.5%
节日装饰：18%
车库/储物区：18%
应急准备：15%
游戏玩家：15%
汽车后备箱：12%

这样视觉上能覆盖最大人群与最高频场景。
"""

    # 写入三个详情行（对标 / 集群 / 差异化），satisfy_condition 留空
    benchmark_demo = (refus_text + "\n\n" + voc_text).strip()[:8000]
    AnalysisDetail.objects.create(
        analysis=asin_obj,
        category="benchmark",
        gpt_summary=benchmark_demo,
        satisfy_condition="",
    )
    AnalysisDetail.objects.create(
        analysis=asin_obj,
        category="cluster",
        gpt_summary=negative_text.strip(),
        satisfy_condition="",
    )
    AnalysisDetail.objects.create(
        analysis=asin_obj,
        category="differentiation",
        gpt_summary=suggestions_text.strip(),
        satisfy_condition="",
    )

    print("✅ 已成功写入 ASIN B01B8R6PF2 的完整分析数据。")

if __name__ == '__main__':
    insert_data()