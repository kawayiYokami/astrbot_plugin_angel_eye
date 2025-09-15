"""
维基百科API客户端，用于执行搜索和获取页面内容。
继承自BaseWikiClient基类以减少代码重复。
"""

from typing import List, Optional, Dict
from .base_client import BaseWikiClient
from astrbot.api import logger



class WikipediaClient(BaseWikiClient):
    """维基百科客户端"""

    def __init__(self, config: Dict):
        super().__init__(
            api_endpoint="https://zh.wikipedia.org/w/api.php",
            site_name="WikipediaClient",
            config=config
        )

    async def search(self, keyword: str, limit: int = 5) -> List[Dict[str, str]]:
        """
        根据关键词搜索维基百科。
        为了与 Retriever 兼容，返回值为 List[Dict[str, str]]。

        :param keyword: 搜索的关键词
        :param limit: 返回结果的最大数量
        :return: 搜索结果列表，如果请求失败则返回空列表
        """
        params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": keyword,
            "srlimit": limit,
            # 关键参数：请求返回摘要信息
            "srprop": "snippet"
        }

        data = await self._make_request(params)
        if not data or "query" not in data or "search" not in data["query"]:
            return []

        results = []
        for item in data["query"]["search"]:
            pageid = item['pageid']
            # 构造维基百科的页面URL
            url = f"https://zh.wikipedia.org/?curid={pageid}"
            # 返回 Retriever 需要的字典格式
            results.append({
                "title": item['title'],
                "url": url,
                "pageid": pageid,
                # 获取API返回的摘要信息
                "snippet": item.get('snippet', '')
            })
        return results

    async def get_page_content(self, title: str, pageid: int = None) -> Optional[str]:
        """
        根据页面ID获取页面的wikitext内容。
        为了兼容 Retriever，保留了 title 参数，但优先使用 pageid。

        :param title: 词条标题 (备用)
        :param pageid: 维基百科页面的ID (首选)
        :return: 页面的wikitext内容，如果请求失败则返回None
        """
        if not pageid:
            logger.warning(f"AngelEye[{self.site_name}]: 调用 get_page_content 时未提供 pageid，无法获取内容。Title: {title}")
            return None

        params = {
            "action": "parse",
            "format": "json",
            "pageid": pageid,
            # 获取展开模板后的 Wikitext
            "prop": "wikitext"
        }

        data = await self._make_request(params)
        if not data or "parse" not in data or "wikitext" not in data["parse"]:
            logger.warning(f"AngelEye[{self.site_name}]: 在 pageid {pageid} 的响应中未找到 wikitext。")
            return None

        return data["parse"]["wikitext"]["*"]