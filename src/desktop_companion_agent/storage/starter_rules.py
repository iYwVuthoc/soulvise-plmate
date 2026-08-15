"""可解释、可恢复的首批监督规则。"""

from __future__ import annotations

from desktop_companion_agent.models import CognitionRule, RuleMode, RuleSource

STARTER_RULES_VERSION = "1"
STARTER_RULES_METADATA_KEY = "starter_supervision_rules_version"


def starter_supervision_rules() -> list[CognitionRule]:
    """每次返回全新的固定ID规则，供首次初始化和手动恢复使用。"""

    return [
        CognitionRule(
            id="builtin.supervision.sexual-services",
            mode=RuleMode.SUPERVISION,
            title="色情与成人服务引流",
            description="拦截以色情内容或成人服务招揽为目的的页面。",
            keywords=[
                "成人视频",
                "色情直播",
                "裸聊",
                "付费裸聊",
                "约炮",
                "招嫖",
                "性服务",
                "成人资源群",
            ],
            exclusions=["性教育", "两性健康", "医学科普", "案件新闻"],
            priority=95,
            source=RuleSource.BUILTIN,
        ),
        CognitionRule(
            id="builtin.supervision.vulgar-suggestive",
            mode=RuleMode.SUPERVISION,
            title="低俗擦边与性暗示",
            description="识别明显以挑逗、擦边或私密福利引流为目的的内容。",
            keywords=[
                "擦边直播",
                "擦边视频",
                "软色情",
                "低俗挑逗",
                "性暗示挑战",
                "私密福利视频",
            ],
            exclusions=["服装穿搭", "舞蹈教学", "艺术摄影", "新闻评论"],
            priority=85,
            source=RuleSource.BUILTIN,
        ),
        CognitionRule(
            id="builtin.supervision.graphic-violence",
            mode=RuleMode.SUPERVISION,
            title="血腥暴力特写",
            description="识别没有必要遮挡、以强刺激特写为主要内容的血腥暴力画面。",
            keywords=[
                "血腥现场",
                "尸体特写",
                "肢解视频",
                "斩首视频",
                "虐杀视频",
                "事故现场无打码",
                "重伤流血特写",
                "动物虐杀",
            ],
            exclusions=[
                "急救教学",
                "医学科普",
                "历史纪录片",
                "已打码正规新闻",
                "影视特效制作",
            ],
            priority=95,
            source=RuleSource.BUILTIN,
        ),
        CognitionRule(
            id="builtin.supervision.shock-content",
            mode=RuleMode.SUPERVISION,
            title="过度猎奇与精神污染",
            description=(
                "仅匹配以恶心、惊吓或强烈精神冲击为目的的内容；医学、生物科普、"
                "正规新闻、影视化妆和特效制作默认例外。"
            ),
            keywords=[
                "重口猎奇",
                "精神污染视频",
                "恶心挑战",
                "慎入重口",
                "极度不适画面",
                "生理不适警告",
            ],
            exclusions=["医学科普", "生物科普", "正规新闻", "影视化妆", "特效制作"],
            priority=80,
            source=RuleSource.BUILTIN,
        ),
        CognitionRule(
            id="builtin.supervision.political-example",
            mode=RuleMode.SUPERVISION,
            title="自定义政治规则示例",
            description=(
                "默认关闭。请由用户填写具体关键词、正例和例外；软件不会自行定义政治敏感。"
            ),
            examples=["正例：填写你明确希望提醒的具体内容"],
            exclusions=["例外：填写不应触发的新闻、学习或讨论场景"],
            priority=60,
            source=RuleSource.BUILTIN,
            enabled=False,
        ),
    ]

