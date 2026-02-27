"""
天使之眼插件 - 主入口文件
重构后，本插件仅提供一个函数工具：QQHistorySearchTool。
"""

from astrbot.api.star import Star, Context, register
from .tools.qq_history_tool import QQHistorySearchTool

# 导入 logger
try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

@register(
    "astrbot_plugin_angel_eye",
    "kawayiYokami",
    "一个为 AstrBot 设计的函数工具插件，允许大语言模型（LLM）直接查询当前群聊的聊天记录。",
    "1.0.5",
    "https://github.com/kawayiYokami/astrbot_plugin_angel_eye"
)
class AngelEyePlugin(Star):
    """
    天使之眼插件，提供查询QQ群聊历史记录的函数工具。
    """

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)

        # 先清理可能存在的同名工具，避免重复声明
        try:
            self.context.provider_manager.llm_tools.remove_func("search_qq_chat_history")
            logger.info("AngelEyePlugin: 清理了已存在的同名工具")
        except Exception:
            pass  # 如果没有同名工具也不报错，静默处理

        # 实例化并注册工具
        self.qq_history_tool = QQHistorySearchTool()
        self.context.add_llm_tools(self.qq_history_tool)
        logger.info("AngelEyePlugin: QQHistorySearchTool 已注册。")

    async def terminate(self):
        '''可选择实现 terminate 函数，当插件被卸载/停用时会调用。'''
        try:
            # 移除已注册的工具，避免重复声明错误
            self.context.provider_manager.llm_tools.remove_func(self.qq_history_tool.name)
            logger.info("AngelEyePlugin: QQHistorySearchTool 已取消注册。")
        except Exception as e:
            logger.warning(f"AngelEyePlugin: 取消注册工具时出错: {e}")
        try:
            self.qq_history_tool.history_service.close()
            logger.info("AngelEyePlugin: QQ 历史仓储连接已关闭。")
        except Exception as e:
            logger.warning(f"AngelEyePlugin: 关闭历史仓储连接时出错: {e}")

        logger.info("AngelEyePlugin: 插件已终止。")
