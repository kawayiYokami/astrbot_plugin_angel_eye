"""
萌娘百科API客户端，用于执行搜索操作。
"""

import httpx
from typing import List, Optional
from .models import SearchResult

class MoeGirlSearchClient:
    """萌娘百科搜索客户端"""

    def __init__(self):
        self.API_ENDPOINT = "https://zh.moegirl.org.cn/api.php"
        # 推荐的 User-Agent，遵循 MediaWiki API 礼仪
        self.HEADERS = {
            "User-Agent": "AstrBot-MoeGirlSearchPlugin/1.0 (https://github.com/kawayiYokami/astrbot)"
        }

    async def search(self, keyword: str, limit: int = 5) -> Optional[List[SearchResult]]:
        """
        根据关键词搜索萌娘百科

        :param keyword: 搜索的关键词
        :param limit: 返回结果的最大数量
        :return: 搜索结果列表，如果请求失败则返回None
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
            # 使用自定义 headers 和超时设置
            timeout = httpx.Timeout(10.0) # 10秒超时
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=timeout) as client:
                response = await client.get(self.API_ENDPOINT, params=params)
                response.raise_for_status()  # 如果HTTP状态码不是2xx，则抛出异常
                data = response.json()

            if "query" not in data or "search" not in data["query"]:
                return []

            results = []
            for item in data["query"]["search"]:
                pageid = item['pageid']
                # 构建完整的URL
                url = f"https://zh.moegirl.org.cn/index.php?curid={pageid}"

                results.append(SearchResult(
                    title=item['title'],
                    pageid=pageid,
                    snippet=item.get('snippet', ''), # 使用get以避免KeyError
                    url=url
                ))
            return results
        except httpx.HTTPStatusError as e:
            # 在实际项目中，应该使用 logger 记录错误
            # logger.error(f"请求萌娘百科API失败: {e}")
            print(f"HTTP Error while searching Moegirl: {e}")
            return None
        except Exception as e:
            # logger.error(f"处理萌娘百科搜索时发生未知错误: {e}")
            print(f"Unexpected error while searching Moegirl: {e}")
            return None

    async def fetch_page_content_by_pageid(self, pageid: int) -> Optional[str]:
        """
        根据页面ID获取页面的wikitext内容。

        :param pageid: 萌娘百科页面的ID
        :return: 页面的wikitext内容，如果请求失败则返回None
        """
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
                print(f"No wikitext found for pageid {pageid}")
                return None

            return data["parse"]["wikitext"]["*"]
        except httpx.HTTPStatusError as e:
            print(f"HTTP Error while fetching page content for pageid {pageid}: {e}")
            print(f"Response text: {e.response.text[:200]}...") # 打印部分响应文本以帮助调试
            return None
        except Exception as e:
            print(f"Unexpected error while fetching page content for pageid {pageid}: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc() # 打印完整的堆栈跟踪
            return None