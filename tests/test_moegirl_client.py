#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试 MoegirlClient 的核心功能：搜索和获取页面内容。
此脚本可以直接从命令行运行，以验证客户端是否能正常工作。
"""

import sys
import os
import asyncio

# 获取当前脚本的目录
current_dir = os.path.dirname(os.path.abspath(__file__))
# 获取插件根目录 (tests 的上一级目录)
plugin_root = os.path.dirname(current_dir)
# 将插件根目录添加到 Python 路径中
sys.path.insert(0, plugin_root)

# 使用绝对导入
from clients.moegirl_client import MoegirlClient


async def main():
    """
    主测试函数。
    """
    print("=== 开始测试 MoegirlClient ===")

    # 1. 实例化客户端
    client = MoegirlClient()
    print("1. MoegirlClient 实例化成功。")

    # 2. 测试搜索功能
    test_query = "芙宁娜"
    print(f"\n2. 测试搜索功能，关键词: '{test_query}'")
    try:
        search_results = await client.search(test_query, limit=5)
        if not search_results:
            print("   搜索返回空结果。")
            return
        print(f"   搜索成功，返回 {len(search_results)} 个结果:")
        for i, item in enumerate(search_results):
            print(f"     [{i+1}] 标题: {item.get('title', 'N/A')}, URL: {item.get('url', 'N/A')}, PageID: {item.get('pageid', 'N/A')}")
    except Exception as e:
        print(f"   搜索过程中发生错误: {e}")
        return

    # 3. 测试获取页面内容功能
    # 选择第一个搜索结果进行测试
    if search_results:
        first_result = search_results[0]
        selected_title = first_result.get('title')
        selected_pageid = first_result.get('pageid')
        print(f"\n3. 测试获取页面内容，标题: '{selected_title}', PageID: {selected_pageid}")
        try:
            # 使用 pageid 调用
            page_content = await client.get_page_content(title=selected_title, pageid=selected_pageid)
            if page_content:
                # 为了便于查看，只打印前500个字符
                preview = page_content[:500] + "..." if len(page_content) > 500 else page_content
                print(f"   页面内容获取成功，长度: {len(page_content)} 字符")
                print(f"   内容预览:\n{preview}\n")
            else:
                print("   页面内容为空或获取失败。")
        except Exception as e:
            print(f"   获取页面内容时发生错误: {e}")

    print("=== MoegirlClient 测试结束 ===")


if __name__ == "__main__":
    asyncio.run(main())