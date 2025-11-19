"""
天使之眼插件 - 主入口文件
重构后，本插件仅提供一个函数工具：QQHistorySearchTool。
"""

from astrbot.api.star import Star, Context
from .tools.qq_history_tool import QQHistorySearchTool

# 导入 logger
try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

class AngelEyePlugin(Star):
    """
    天使之眼插件，提供查询QQ群聊历史记录的函数工具。
    """
    name = "AngelEyeTool"
    description = "提供查询QQ群聊历史记录的函数工具。"
    usage = "这是一个函数工具插件，由LLM自动调用。"

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        # 实例化并注册工具
        qq_history_tool = QQHistorySearchTool()
        self.context.add_llm_tools(qq_history_tool)
        logger.info("AngelEyePlugin: QQHistorySearchTool 已注册。")
