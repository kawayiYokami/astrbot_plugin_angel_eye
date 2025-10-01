"""
测试 Angel Eye 插件的主模块功能
"""
import sys
import os
import json
import time

# 添加插件路径到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import AngelEyePlugin


class MockEvent:
    """模拟 AstrMessageEvent"""
    def __init__(self, has_angelheart_context=False, needs_search=True):
        self.unified_msg_origin = "test_chat_id"
        if has_angelheart_context:
            angelheart_data = {
                "chat_records": [
                    {
                        "role": "user",
                        "content": "你好，今天天气怎么样？",
                        "sender_id": "123456",
                        "sender_name": "小明",
                        "timestamp": time.time() - 30
                    },
                    {
                        "role": "assistant",
                        "content": "今天天气晴朗，温度25度",
                        "sender_id": "bot",
                        "sender_name": "AngelHeart",
                        "timestamp": time.time() - 20
                    }
                ],
                "secretary_decision": {
                    "should_reply": True,
                    "reply_strategy": "友好回复",
                    "topic": "天气"
                },
                "needs_search": needs_search
            }
            self.angelheart_context = json.dumps(angelheart_data)


class MockContext:
    """模拟插件上下文"""
    def get_provider_by_id(self, model_id):
        return None  # 简化测试，不实际调用模型


def test_get_dialogue_records_with_angelheart():
    """测试使用天使之心上下文获取对话记录"""
    print("=== 测试 _get_dialogue_records 方法（天使之心上下文）===")
    
    # 创建插件实例
    plugin = AngelEyePlugin(MockContext())
    
    # 测试用例1：有天使之心上下文且需要搜索
    event = MockEvent(has_angelheart_context=True, needs_search=True)
    req_contexts = []
    original_prompt = "当前消息"
    
    result = plugin._get_dialogue_records(event, req_contexts, original_prompt)
    print(f"天使之心上下文结果: {len(result)} 条记录")
    assert len(result) == 2  # 应该有2条格式化记录
    assert "[群友: 小明 (ID: 123456)]" in result[0]
    assert "[助理: AngelHeart]" in result[1]
    print("✓ 天使之心上下文测试通过")
    
    # 测试用例2：有天使之心上下文但不需要搜索
    event = MockEvent(has_angelheart_context=True, needs_search=False)
    result = plugin._get_dialogue_records(event, req_contexts, original_prompt)
    print(f"不需要搜索的结果: {len(result)} 条记录")
    assert len(result) == 0  # 应该返回空列表
    print("✓ 不需要搜索测试通过")
    
    print("\n✅ 天使之心上下文测试完成！")


def test_get_dialogue_records_without_angelheart():
    """测试没有天使之心上下文时获取对话记录"""
    print("\n=== 测试 _get_dialogue_records 方法（无天使之心上下文）===")
    
    # 创建插件实例
    plugin = AngelEyePlugin(MockContext())
    
    # 测试用例：没有天使之心上下文
    event = MockEvent(has_angelheart_context=False)
    req_contexts = [
        {"role": "user", "content": "历史消息1"},
        {"role": "assistant", "content": "历史回复1"}
    ]
    original_prompt = "当前消息"
    
    result = plugin._get_dialogue_records(event, req_contexts, original_prompt)
    print(f"无天使之心上下文结果: {len(result)} 条记录")
    assert len(result) == 1  # 应该返回1个格式化后的对话字符串
    assert "当前消息" in result[0]
    print("✓ 无天使之心上下文测试通过")
    
    print("\n✅ 无天使之心上下文测试完成！")


def test_error_handling():
    """测试错误处理"""
    print("\n=== 测试错误处理 ===")
    
    plugin = AngelEyePlugin(MockContext())
    
    # 测试用例：无效的天使之心上下文
    class InvalidEvent:
        def __init__(self):
            self.unified_msg_origin = "test_chat_id"
            self.angelheart_context = "invalid json"
    
    event = InvalidEvent()
    req_contexts = []
    original_prompt = "当前消息"
    
    result = plugin._get_dialogue_records(event, req_contexts, original_prompt)
    print(f"无效JSON结果: {len(result)} 条记录")
    # 应该回退到现有逻辑
    assert len(result) == 1
    print("✓ 错误处理测试通过")
    
    print("\n✅ 错误处理测试完成！")


if __name__ == "__main__":
    test_get_dialogue_records_with_angelheart()
    test_get_dialogue_records_without_angelheart()
    test_error_handling()
    print("\n🎉 所有主模块测试完成！")
