"""
端到端集成测试，使用虚假的LLM Provider来测试完整的数据检索流程。
此测试会触发真实的网络请求（萌娘百科、维基百科、维基数据），
但通过模拟LLM来避免真实的AI调用。
"""

import asyncio
import json
import sys
import os
from typing import Dict, Any, Optional, List
from unittest.mock import MagicMock

# 将项目根目录添加到 Python 路径中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# 先模拟 astrbot 模块，然后再导入其他模块
sys.modules['astrbot'] = MagicMock()
sys.modules['astrbot.api'] = MagicMock()
sys.modules['astrbot.api.provider'] = MagicMock()

# 创建一个模拟的 Provider 基类
class Provider:
    """模拟的 Provider 基类"""
    async def text_chat(self, prompt: str, **kwargs):
        raise NotImplementedError

# 将模拟的 Provider 注入到 astrbot.api.provider 模块
sys.modules['astrbot.api.provider'].Provider = Provider

from ..models.request import KnowledgeRequest
from ..roles.smart_retriever import SmartRetriever


class FakeLLMProvider(Provider):
    """
    一个虚假的LLM Provider，用于模拟AI行为。
    它根据输入的prompt特征返回预设的响应。
    """

    async def text_chat(self, prompt: str, **kwargs) -> Any:
        """
        模拟LLM的text_chat方法。
        根据prompt的内容返回不同的模拟响应。
        """
        # 创建一个模拟的响应对象
        response = MagicMock()

        # 1. 模拟 Classifier 的行为
        if "## 对话记录:" in prompt or "分析对话" in prompt:
            # 构建一个符合 classifier_prompt.md 格式的响应
            response_json = {
                "required_docs": {
                    "原神": "moegirl",
                    "长城": "wikipedia"
                },
                "required_facts": [
                    "纽约.坐标"
                ]
            }
            # 模拟完整的LLM输出格式（包含思考过程和JSON）
            response_text = f"""我的思考过程:
用户提到了"原神"（一个游戏），应该查询萌娘百科。
用户提到了"长城"（历史建筑），应该查询维基百科。
用户询问了"纽约的坐标"，这是一个精确的事实查询。

---JSON---
{json.dumps(response_json, ensure_ascii=False)}"""
            response.completion_text = response_text

        # 2. 模拟 Filter 的行为
        elif "请从以下搜索结果中选择" in prompt or "选择最佳词条" in prompt:
            # Filter 总是选择第一个结果
            # 需要从prompt中提取搜索结果列表
            if "search_results" in prompt or "候选" in prompt:
                # 简单返回第一个的指示
                response.completion_text = "选择第1个结果"
            else:
                response.completion_text = "选择第一个"

        # 3. 模拟 Summarizer 的行为
        elif "请为以下内容生成" in prompt or "归纳" in prompt or "摘要" in prompt:
            # 从prompt中找到要摘要的内容
            # 通常内容会在"内容:"或类似标记之后
            content_start = prompt.find("内容:")
            if content_start == -1:
                content_start = prompt.find("原文:")
            if content_start == -1:
                content_start = prompt.find("文本:")
            if content_start == -1:
                # 如果找不到标记，就取prompt的一部分
                content_to_summarize = prompt[-500:] if len(prompt) > 500 else prompt
            else:
                content_to_summarize = prompt[content_start:]

            # 无脑提取前100个字符作为摘要
            summary = content_to_summarize[:100]
            response.completion_text = summary

        # 默认响应
        else:
            response.completion_text = ""

        return response


async def test_real_retrieval_flow():
    """
    测试真实的检索流程。
    使用虚假的LLM Provider，但会触发真实的网络请求。
    """
    print("\n" + "="*60)
    print("开始端到端集成测试（使用虚假LLM）")
    print("="*60)

    # 1. 准备：创建虚假的LLM Provider
    fake_llm = FakeLLMProvider()
    print("\n✓ 已创建虚假LLM Provider")

    # 2. 初始化：创建 SmartRetriever 实例，注入虚假LLM
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
        retriever = SmartRetriever(analyzer_provider=fake_llm, config=config)
        print("✓ 已初始化 SmartRetriever")
    except Exception as e:
        print(f"✗ 初始化 SmartRetriever 失败: {e}")
        return

    # 3. 构建知识请求：直接创建包含三个查询任务的请求
    knowledge_request = KnowledgeRequest(
        required_docs={
            "原神": "moegirl",
            "长城": "wikipedia"
        },
        required_facts=[
            "纽约.坐标"
        ]
    )
    print("\n✓ 已构建知识请求:")
    print(f"  - 文档查询: {knowledge_request.required_docs}")
    print(f"  - 事实查询: {knowledge_request.required_facts}")

    # 4. 执行：调用核心检索方法，触发真实的网络请求
    print("\n开始执行检索（将进行真实的网络请求）...")
    print("-" * 40)

    try:
        knowledge_result = await retriever.retrieve(knowledge_request)
        print("\n✓ 检索完成")
    except Exception as e:
        print(f"\n✗ 检索失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # 5. 验证与输出结果
    print("\n" + "="*60)
    print("检索结果")
    print("="*60)

    if not knowledge_result:
        print("✗ 未返回任何结果")
        return

    if not knowledge_result.chunks:
        print("✗ 结果中没有知识片段")
        return

    print(f"\n共获取到 {len(knowledge_result.chunks)} 个知识片段:\n")

    # 统计各个来源的结果
    found_genshin = False
    found_great_wall = False
    found_nyc = False

    for i, chunk in enumerate(knowledge_result.chunks, 1):
        print(f"\n【片段 {i}】")
        print(f"来源: {chunk.source}")
        print(f"实体: {chunk.entity}")
        if chunk.source_url:
            print(f"URL: {chunk.source_url}")

        # 打印内容预览（限制长度）
        content_preview = chunk.content[:200] if len(chunk.content) > 200 else chunk.content
        print(f"内容预览:\n{content_preview}")
        if len(chunk.content) > 200:
            print(f"... (共 {len(chunk.content)} 字符)")

        print("-" * 40)

        # 检查是否找到了所有请求的实体
        if chunk.entity == "原神":
            found_genshin = True
        if chunk.entity == "长城":
            found_great_wall = True
        if chunk.entity == "纽约":
            found_nyc = True

    # 6. 验证测试结果
    print("\n" + "="*60)
    print("测试验证")
    print("="*60)

    success = True

    if found_genshin:
        print("✓ 成功获取萌娘百科的'原神'数据")
    else:
        print("✗ 未能获取萌娘百科的'原神'数据")
        success = False

    if found_great_wall:
        print("✓ 成功获取维基百科的'长城'数据")
    else:
        print("✗ 未能获取维基百科的'长城'数据")
        success = False

    if found_nyc:
        print("✓ 成功获取维基数据的'纽约坐标'")
    else:
        print("✗ 未能获取维基数据的'纽约坐标'")
        success = False

    print("\n" + "="*60)
    if success:
        print("✅ 测试成功！所有请求的数据都已获取")
    else:
        print("❌ 测试失败！部分数据未能获取")
    print("="*60)

    return success


if __name__ == "__main__":
    # 运行测试
    result = asyncio.run(test_real_retrieval_flow())

    # 设置退出码
    exit(0 if result else 1)