"""
使用真实本地LLM进行端到端测试
调用本地部署的Gemini模型来测试完整的知识检索流程
"""

import asyncio
import json
import sys
import os
import aiohttp
from typing import Dict, Any, Optional, List

# 将项目根目录添加到 Python 路径中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# 模拟 astrbot 模块
sys.modules['astrbot'] = type(sys)('astrbot')
sys.modules['astrbot.api'] = type(sys)('astrbot.api')
sys.modules['astrbot.api.provider'] = type(sys)('astrbot.api.provider')


class LocalLLMProvider:
    """
    本地LLM Provider，调用本地部署的Gemini模型
    """
    def __init__(self, base_url: str = "http://127.0.0.1:7861", api_key: str = "123qwe", model: str = "gemini-2.5-flash-lite"):
        """
        初始化本地LLM Provider

        Args:
            base_url: 本地LLM服务地址
            api_key: API密钥
            model: 模型名称
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    async def text_chat(self, prompt: str, **kwargs) -> Any:
        """
        调用本地LLM进行文本对话

        Args:
            prompt: 输入提示词

        Returns:
            包含completion_text的响应对象
        """
        # 构建请求数据
        data = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 2000)
        }

        # 发送请求到本地LLM
        async with aiohttp.ClientSession() as session:
            try:
                url = f"{self.base_url}/v1/chat/completions"
                print(f"正在调用本地LLM: {url}")

                async with session.post(url, json=data, headers=self.headers) as response:
                    if response.status == 200:
                        result = await response.json()
                        # 提取响应文本
                        completion_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")

                        # 创建响应对象
                        response_obj = type('Response', (), {})()
                        response_obj.completion_text = completion_text

                        print(f"LLM响应长度: {len(completion_text)} 字符")
                        return response_obj
                    else:
                        error_text = await response.text()
                        print(f"LLM调用失败 ({response.status}): {error_text}")
                        # 返回一个空响应
                        response_obj = type('Response', (), {})()
                        response_obj.completion_text = ""
                        return response_obj

            except Exception as e:
                print(f"调用LLM时发生错误: {e}")
                # 返回一个空响应
                response_obj = type('Response', (), {})()
                response_obj.completion_text = ""
                return response_obj


# 注入Provider基类
sys.modules['astrbot.api.provider'].Provider = LocalLLMProvider

from ..models.request import KnowledgeRequest
from ..roles.smart_retriever import SmartRetriever


async def test_real_llm_flow():
    """
    使用真实的本地LLM测试完整流程
    """
    print("\n" + "="*80)
    print("开始真实LLM端到端测试（使用本地Gemini模型）")
    print("="*80)

    # 1. 创建本地LLM Provider
    print("\n1. 初始化本地LLM Provider")
    print("   地址: http://127.0.0.1:7861")
    print("   模型: gemini-2.5-flash-lite")

    local_llm = LocalLLMProvider(
        base_url="http://127.0.0.1:7861",
        api_key="123qwe",
        model="gemini-2.5-flash-lite"
    )
    print("   ✓ Provider初始化完成")

    # 2. 测试LLM连接
    print("\n2. 测试LLM连接")
    test_response = await local_llm.text_chat("你好，请回复'我在这里'")
    if test_response.completion_text:
        print(f"   ✓ LLM响应: {test_response.completion_text[:50]}...")
    else:
        print("   ✗ 无法连接到本地LLM，请检查服务是否运行")
        return False

    # 3. 初始化SmartRetriever
    print("\n3. 初始化SmartRetriever")
    config = {
        "moegirl_enabled": True,
        "wikipedia_enabled": True,
        "wikidata_enabled": True,
        "retrieval": {
            "text_length_threshold": 500,
            "max_search_results": 5
        }
    }

    try:
        retriever = SmartRetriever(analyzer_provider=local_llm, config=config)
        print("   ✓ SmartRetriever初始化完成")
    except Exception as e:
        print(f"   ✗ 初始化失败: {e}")
        return False

    # 4. 构建测试请求
    print("\n4. 构建知识请求")
    knowledge_request = KnowledgeRequest(
        required_docs={
            "原神": "moegirl",
            "长城": "wikipedia"
        },
        required_facts=[
            "纽约.坐标"
        ]
    )
    print(f"   文档查询: {knowledge_request.required_docs}")
    print(f"   事实查询: {knowledge_request.required_facts}")

    # 5. 执行检索（使用真实LLM）
    print("\n5. 执行检索流程")
    print("   注意：这将使用真实的LLM进行Filter和Summarizer操作")
    print("-" * 60)

    try:
        knowledge_result = await retriever.retrieve(knowledge_request)
        print("\n   ✓ 检索完成")
    except Exception as e:
        print(f"\n   ✗ 检索失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    # 6. 展示结果
    print("\n" + "="*80)
    print("检索结果（使用真实LLM）")
    print("="*80)

    if not knowledge_result or not knowledge_result.chunks:
        print("   ✗ 未返回任何结果")
        return False

    print(f"\n共获取到 {len(knowledge_result.chunks)} 个知识片段:\n")

    found_items = {"原神": False, "长城": False, "纽约": False}

    for i, chunk in enumerate(knowledge_result.chunks, 1):
        print(f"\n【片段 {i}】")
        print(f"来源: {chunk.source}")
        print(f"实体: {chunk.entity}")
        if chunk.source_url:
            print(f"URL: {chunk.source_url}")

        # 打印内容预览
        content_preview = chunk.content[:300] if len(chunk.content) > 300 else chunk.content
        print(f"内容预览:\n{content_preview}")
        if len(chunk.content) > 300:
            print(f"... (共 {len(chunk.content)} 字符)")

        print("-" * 60)

        # 检查找到的实体
        if chunk.entity in found_items:
            found_items[chunk.entity] = True

    # 7. 测试验证
    print("\n" + "="*80)
    print("测试验证")
    print("="*80)

    all_success = True
    for entity, found in found_items.items():
        if found:
            print(f"   ✓ 成功获取 '{entity}' 的数据（通过真实LLM处理）")
        else:
            print(f"   ✗ 未能获取 '{entity}' 的数据")
            all_success = False

    # 8. 总结
    print("\n" + "="*80)
    if all_success:
        print("✅ 真实LLM测试成功！")
        print("说明：所有Filter筛选和Summarizer摘要都由本地Gemini模型处理")
    else:
        print("⚠️ 真实LLM测试部分失败")
        print("提示：请检查本地LLM服务是否正常运行")
    print("="*80)

    return all_success


async def test_llm_only():
    """
    仅测试LLM连接和基本功能
    """
    print("\n" + "="*60)
    print("LLM连接测试")
    print("="*60)

    llm = LocalLLMProvider()

    # 测试不同类型的提示
    test_prompts = [
        ("基本对话", "请说'测试成功'"),
        ("JSON生成", '请返回一个JSON: {"status": "ok", "message": "test"}'),
        ("列表选择", "从以下选项中选择第一个：1.苹果 2.香蕉 3.橙子")
    ]

    for test_name, prompt in test_prompts:
        print(f"\n测试: {test_name}")
        print(f"提示: {prompt}")

        response = await llm.text_chat(prompt)
        if response.completion_text:
            print(f"响应: {response.completion_text[:100]}...")
        else:
            print("响应: (无)")

    print("\n" + "="*60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="真实LLM测试")
    parser.add_argument("--llm-only", action="store_true", help="仅测试LLM连接")
    args = parser.parse_args()

    if args.llm_only:
        # 仅测试LLM
        asyncio.run(test_llm_only())
    else:
        # 完整测试
        result = asyncio.run(test_real_llm_flow())
        exit(0 if result else 1)