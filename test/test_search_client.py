"""
萌娘百科搜索客户端的测试脚本
"""

import asyncio
import sys
import os
import json

# 将插件的根目录添加到Python路径中，以便能正确导入模块
plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, plugin_root)

from core.search_client import MoeGirlSearchClient
from core.models import SearchResult

async def test_search_client():
    """测试 MoeGirlSearchClient 的功能"""
    client = MoeGirlSearchClient()

    # 1. 测试搜索功能
    print("--- 测试搜索功能 ---")
    keyword = "初音未来"
    print(f"正在搜索关键词: {keyword}")

    search_results = await client.search(keyword, limit=2)

    if search_results is None:
        print("搜索失败，返回了 None")
        return

    if not search_results:
        print("搜索完成，但没有找到相关结果。")
        return

    print(f"\n找到 {len(search_results)} 个结果:")

    # 将搜索结果保存到文件
    search_results_data = []
    for result in search_results:
        search_results_data.append({
            "title": result.title,
            "pageid": result.pageid,
            "snippet": result.snippet,
            "url": result.url
        })
    with open("search_results.json", "w", encoding="utf-8") as f:
        json.dump(search_results_data, f, ensure_ascii=False, indent=4)
    print("搜索结果已保存到 search_results.json")

    for i, result in enumerate(search_results, 1):
        print(f"\n--- 搜索结果 {i} ---")
        print(f"标题: {result.title}")
        print(f"PageID: {result.pageid}")
        print(f"URL: {result.url}")
        # print(f"摘要: {result.snippet[:50]}...")

    # 2. 测试阅读功能 (读取第一个结果)
    print("\n\n--- 测试阅读功能 ---")
    first_result = search_results[0]
    print(f"正在读取页面: {first_result.title} (PageID: {first_result.pageid})")

    content = await client.fetch_page_content_by_pageid(first_result.pageid)

    if content is None:
        print("阅读失败，返回了 None")
        return

    print(f"\n成功获取页面内容，前200个字符预览:")
    print(content[:200])
    print("\n... (内容截断)")

    # 将页面内容保存到文件
    with open("page_content.txt", "w", encoding="utf-8") as f:
        f.write(content)
    print("页面内容已保存到 page_content.txt")

if __name__ == "__main__":
    asyncio.run(test_search_client())