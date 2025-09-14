"""
示例脚本，演示如何使用 core/search_client.py 和 core/wikitext_cleaner.py
来搜索并清理萌娘百科的页面内容。
"""

import asyncio
import json
from core.search_client import MoeGirlSearchClient
from core.wikitext_cleaner import clean


async def main():
    """主函数，执行搜索、获取和清理流程。"""
    print("Starting example usage...")

    # 1. 创建搜索客户端
    client = MoeGirlSearchClient()

    # 2. 搜索"甘雨"
    print("Searching for '甘雨'...")
    search_results = await client.search("甘雨", limit=3)

    if not search_results:
        print("No search results found for '甘雨'.")
        return

    print(f"Found {len(search_results)} results.")
    for i, result in enumerate(search_results):
        print(f"  {i+1}. {result.title} (ID: {result.pageid})")

    # 3. 获取第一个结果的页面内容 (wikitext)
    first_result = search_results[0]
    print(f"\nFetching content for page '{first_result.title}' (ID: {first_result.pageid})...")
    wikitext_content = await client.fetch_page_content_by_pageid(first_result.pageid)

    if not wikitext_content:
        print("Failed to fetch wikitext content.")
        return

    print(f"Successfully fetched wikitext content. Length: {len(wikitext_content)} characters.")

    # 4. 清理获取到的 Wikitext
    print("\nCleaning fetched wikitext content...")
    cleaned_content = clean(wikitext_content)

    if not cleaned_content:
        print("Cleaning resulted in empty content.")
        return

    print(f"Successfully cleaned wikitext. Length: {len(cleaned_content)} characters.")

    # 5. 输出清理后的内容的前1000个字符作为示例
    print("\n--- Cleaned Content (first 1000 characters) ---")
    print(cleaned_content[:1000])
    print("--- End of Sample ---")

    print("\nExample usage completed successfully.")


# 确保此脚本作为主程序运行时才执行
if __name__ == "__main__":
    asyncio.run(main())