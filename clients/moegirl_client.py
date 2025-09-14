"""
萌娘百科API客户端，用于执行搜索和获取页面内容。
基于用户提供的正确、健壮的实现。
"""

import httpx
from typing import List, Optional, Dict

from core.log import get_logger
from models.models import SearchResult

logger = get_logger(__name__)

class MoegirlClient:
    """萌娘百科客户端"""

    def __init__(self):
        self.API_ENDPOINT = "https://zh.moegirl.org.cn/api.php"
        # 推荐的 User-Agent，遵循 MediaWiki API 礼仪
        self.HEADERS = {
            "User-Agent": "AstrBot-AngelEyePlugin/1.0 (https://github.com/kawayiYokami/astrbot)"
        }

    async def search(self, keyword: str, limit: int = 5) -> List[Dict[str, str]]:
        """
        根据关键词搜索萌娘百科。
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
            "srprop": "snippet"
        }
        try:
            timeout = httpx.Timeout(10.0)
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=timeout) as client:
                response = await client.get(self.API_ENDPOINT, params=params)
                response.raise_for_status()
                data = response.json()

            if "query" not in data or "search" not in data["query"]:
                return []

            results = []
            for item in data["query"]["search"]:
                pageid = item['pageid']
                url = f"https://zh.moegirl.org.cn/index.php?curid={pageid}"
                # 返回 Retriever 需要的字典格式
                results.append({
                    "title": item['title'],
                    "url": url,
                    "pageid": pageid, # 将 pageid 也返回，以便后续使用
                    "snippet": item.get('snippet', '')
                })
            return results
        except httpx.HTTPStatusError as e:
            logger.error(f"AngelEye[MoegirlClient]: 请求萌娘百科API失败: {e}")
            return []
        except Exception as e:
            logger.error(f"AngelEye[MoegirlClient]: 处理萌娘百科搜索时发生未知错误: {e}", exc_info=True)
            return []

    async def get_page_content(self, title: str, pageid: int = None) -> Optional[str]:
        """
        根据页面ID获取页面的wikitext内容。
        为了兼容 Retriever，保留了 title 参数，但优先使用 pageid。

        :param title: 词条标题 (备用)
        :param pageid: 萌娘百科页面的ID (首选)
        :return: 页面的wikitext内容，如果请求失败则返回None
        """
        if not pageid:
            logger.warning(f"AngelEye[MoegirlClient]: 调用 get_page_content 时未提供 pageid，无法获取内容。Title: {title}")
            return None

        params = {
            "action": "parse",
            "format": "json",
            "pageid": pageid,
            "prop": "wikitext"
        }
        try:
            timeout = httpx.Timeout(10.0)
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=timeout) as client:
                response = await client.get(self.API_ENDPOINT, params=params)
                response.raise_for_status()
                data = response.json()

            if "parse" not in data or "wikitext" not in data["parse"]:
                logger.warning(f"AngelEye[MoegirlClient]: 在 pageid {pageid} 的响应中未找到 wikitext。")
                return None

            return data["parse"]["wikitext"]["*"]
        except httpx.HTTPStatusError as e:
            logger.error(f"AngelEye[MoegirlClient]: 获取 pageid {pageid} 的页面内容时HTTP失败: {e}")
            return None
        except Exception as e:
            logger.error(f"AngelEye[MoegirlClient]: 获取 pageid {pageid} 的页面内容时发生未知错误: {e}", exc_info=True)
            return None