"""
Angel Eye 插件 - 通用百科客户端 (空实现)
"""
from typing import List, Dict, Optional
from astrbot.core import logger

class GeneralClient:
    """
    通用的百科客户端，目前为占位符。
    """
    async def search(self, query: str) -> List[Dict[str, str]]:
        """
        通用搜索的空实现。
        """
        logger.info(f"AngelEye[GeneralClient]: 通用搜索功能暂未实现。查询: {query}")
        return []

    async def get_page_content(self, title: str) -> Optional[str]:
        """
        获取通用百科页面内容的空实现。
        """
        logger.info(f"AngelEye[GeneralClient]: 获取通用页面内容的功能暂未实现。标题: {title}")
        return None