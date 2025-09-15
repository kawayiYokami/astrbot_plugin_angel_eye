"""
完整的端到端对话流程测试
让真实的LLM处理Classifier->Filter->Summarizer的完整流程
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
from ..roles.classifier import Classifier
from ..roles.smart_retriever import SmartRetriever


async def test_conversation_scenario(conversation_history: List[Dict], current_prompt: str, scenario_name: str):
    """
    测试一个对话场景

    Args:
        conversation_history: 对话历史
        current_prompt: 当前用户输入
        scenario_name: 场景名称

    Returns:
        bool: 测试是否成功
    """
    print(f"\n{'='*60}")
    print(f"测试场景: {scenario_name}")
    print(f"{'='*60}")

    # 1. 创建本地LLM Provider
    print("\n1. 初始化本地LLM Provider")
    local_llm = LocalLLMProvider(
        base_url="http://127.0.0.1:7861",
        api_key="",
        model="gemini-2.5-flash-lite"
    )
    print("   ✓ Provider初始化完成")

    # 2. 初始化Classifier
    print("\n2. 初始化Classifier")
    try:
        classifier = Classifier(provider=local_llm)
        print("   ✓ Classifier初始化完成")
    except Exception as e:
        print(f"   ✗ 初始化失败: {e}")
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

    # 4. 执行Classifier分析
    print(f"\n4. 执行Classifier分析")
    print(f"   用户输入: {current_prompt}")

    try:
        knowledge_request = await classifier.classify(conversation_history, current_prompt)
        print("   ✓ Classifier分析完成")

        if knowledge_request:
            print(f"   生成的知识请求:")
            print(f"     文档查询: {knowledge_request.required_docs}")
            print(f"     事实查询: {knowledge_request.required_facts}")
        else:
            print("   未生成知识请求（无需查询）")
            return True

    except Exception as e:
        print(f"   ✗ Classifier分析失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    # 5. 执行检索（使用真实LLM）
    print("\n5. 执行检索流程")
    print("   注意：这将使用真实的LLM进行Filter和Summarizer操作")
    print("-" * 50)

    try:
        knowledge_result = await retriever.retrieve(knowledge_request)
        print("\n   ✓ 检索完成")
    except Exception as e:
        print(f"\n   ✗ 检索失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    # 6. 展示结果
    print(f"\n{'='*60}")
    print("检索结果")
    print(f"{'='*60}")

    if not knowledge_result or not knowledge_result.chunks:
        print("   ✗ 未返回任何结果")
        return False

    print(f"\n共获取到 {len(knowledge_result.chunks)} 个知识片段:\n")

    for i, chunk in enumerate(knowledge_result.chunks, 1):
        print(f"\n【片段 {i}】")
        print(f"来源: {chunk.source}")
        print(f"实体: {chunk.entity}")
        if chunk.source_url:
            print(f"URL: {chunk.source_url}")

        # 打印内容预览
        content_preview = chunk.content[:200] if len(chunk.content) > 200 else chunk.content
        print(f"内容预览:\n{content_preview}")
        if len(chunk.content) > 200:
            print(f"... (共 {len(chunk.content)} 字符)")

        print("-" * 50)

    print(f"\n✅ 场景 '{scenario_name}' 测试成功！")
    return True


async def run_all_conversation_tests():
    """
    运行所有对话场景测试
    """
    print("\n" + "="*80)
    print("开始完整的端到端对话流程测试")
    print("使用真实的本地LLM处理Classifier->Filter->Summarizer完整流程")
    print("="*80)

    # 定义多个测试场景
    test_scenarios = [
        {
            "name": "游戏查询场景",
            "history": [
                {"role": "user", "content": "你好，我想了解一下原神这个游戏"},
                {"role": "assistant", "content": "原神是由米哈游开发的一款开放世界冒险游戏"}
            ],
            "prompt": "能详细介绍一下原神的背景故事和主要角色吗？"
        },
        {
            "name": "历史查询场景",
            "history": [
                {"role": "user", "content": "我在看一些历史纪录片"},
                {"role": "assistant", "content": "历史是很有趣的主题"}
            ],
            "prompt": "帮我查一下长城的历史和建造背景"
        },
        {
            "name": "地理事实查询场景",
            "history": [
                {"role": "user", "content": "我在学习地理知识"},
                {"role": "assistant", "content": "地理学涉及地球表面的自然和人文现象"}
            ],
            "prompt": "纽约的地理坐标是多少？"
        },
        {
            "name": "混合查询场景",
            "history": [
                {"role": "user", "content": "我最近在研究一些文化内容"},
                {"role": "assistant", "content": "文化研究涉及很多有趣的领域"}
            ],
            "prompt": "我想了解来自深渊这部动漫，还有朱祁镇这个历史人物"
        },
        {
            "name": "日常对话场景",
            "history": [
                {"role": "user", "content": "今天的天气真好"},
                {"role": "assistant", "content": "是的，适合出去走走"}
            ],
            "prompt": "你觉得去哪里散步比较好？"
        }
    ]

    # 执行所有测试
    passed = 0
    failed = 0

    for scenario in test_scenarios:
        try:
            success = await test_conversation_scenario(
                scenario["history"],
                scenario["prompt"],
                scenario["name"]
            )
            if success:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"\n❌ 场景 '{scenario['name']}' 执行出错: {e}")
            failed += 1
            import traceback
            traceback.print_exc()

    # 最终总结
    print(f"\n{'='*80}")
    print("完整对话流程测试总结")
    print(f"{'='*80}")
    print(f"总场景数: {len(test_scenarios)}")
    print(f"✅ 成功: {passed}")
    print(f"❌ 失败: {failed}")

    if failed == 0:
        print("\n🎉 所有对话场景测试通过！")
        print("说明：系统成功完成了从对话理解到知识检索的完整流程")
        print("包括：Classifier生成JSON、Filter筛选、Summarizer摘要")
    else:
        print(f"\n⚠️ 有 {failed} 个场景测试失败")

    print(f"{'='*80}")

    return failed == 0


async def test_single_classifier_only():
    """
    仅测试Classifier的JSON生成能力
    """
    print("\n" + "="*60)
    print("Classifier JSON生成能力测试")
    print("="*60)

    # 创建本地LLM Provider
    local_llm = LocalLLMProvider()
    classifier = Classifier(provider=local_llm)

    # 测试不同的输入
    test_inputs = [
        {
            "history": [],
            "prompt": "帮我查一下原神是什么游戏，还有纽约的坐标",
            "description": "混合查询（游戏+地理事实）"
        },
        {
            "history": [{"role": "user", "content": "我在学习历史"}, {"role": "assistant", "content": "历史很有趣"}],
            "prompt": "朱祁镇的父亲是谁？",
            "description": "历史人物事实查询"
        },
        {
            "history": [],
            "prompt": "今天天气不错",
            "description": "日常对话（无需查询）"
        }
    ]

    for i, test_input in enumerate(test_inputs, 1):
        print(f"\n测试 {i}: {test_input['description']}")
        print(f"输入: {test_input['prompt']}")

        try:
            result = await classifier.classify(test_input["history"], test_input["prompt"])
            if result:
                print(f"生成的JSON:")
                print(f"  required_docs: {result.required_docs}")
                print(f"  required_facts: {result.required_facts}")
            else:
                print("  结果: 无需查询")
        except Exception as e:
            print(f"  错误: {e}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="完整对话流程测试")
    parser.add_argument("--classifier-only", action="store_true", help="仅测试Classifier")
    parser.add_argument("--scenario", type=str, help="运行特定场景")
    args = parser.parse_args()

    if args.classifier_only:
        # 仅测试Classifier
        asyncio.run(test_single_classifier_only())
    elif args.scenario:
        # 运行特定场景
        print("暂不支持特定场景运行")
    else:
        # 运行所有测试
        result = asyncio.run(run_all_conversation_tests())
        exit(0 if result else 1)