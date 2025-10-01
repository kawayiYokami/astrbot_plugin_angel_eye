"""
测试 Angel Eye 插件的格式化功能
"""
import sys
import os
import time

# 添加插件路径到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.formatter import format_angelheart_message, format_unified_message


def test_format_angelheart_message():
    """测试天使之心消息格式化功能"""
    print("=== 测试 format_angelheart_message 函数 ===")
    
    # 测试用例1：普通用户消息
    user_message = {
        "role": "user",
        "content": "你好，今天天气怎么样？",
        "sender_id": "123456",
        "sender_name": "小明",
        "timestamp": time.time() - 30  # 30秒前
    }
    
    result = format_angelheart_message(user_message)
    print(f"用户消息格式化结果: {result}")
    assert "[群友: 小明 (ID: 123456)]" in result
    assert "(刚刚)" in result or "(30秒前)" in result
    assert "你好，今天天气怎么样？" in result
    print("✓ 用户消息格式化测试通过")
    
    # 测试用例2：助理消息
    assistant_message = {
        "role": "assistant",
        "content": "今天天气晴朗，温度25度",
        "sender_id": "bot",
        "sender_name": "AngelHeart",
        "timestamp": time.time() - 60  # 1分钟前
    }
    
    result = format_angelheart_message(assistant_message)
    print(f"助理消息格式化结果: {result}")
    assert "[助理: AngelHeart]" in result
    assert "(1分钟前)" in result
    assert "今天天气晴朗，温度25度" in result
    print("✓ 助理消息格式化测试通过")
    
    # 测试用例3：多模态内容
    multimodal_message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "你好"},
            {"type": "image_url", "url": "image.jpg"},
            {"type": "text", "text": "这是图片"}
        ],
        "sender_id": "789012",
        "sender_name": "小红",
        "timestamp": time.time() - 120  # 2分钟前
    }
    
    result = format_angelheart_message(multimodal_message)
    print(f"多模态消息格式化结果: {result}")
    assert "[群友: 小红 (ID: 789012)]" in result
    assert "(2分钟前)" in result
    assert "你好这是图片" in result  # 应该只提取文本内容
    print("✓ 多模态消息格式化测试通过")
    
    # 测试用例4：错误处理
    invalid_message = {
        "role": "user",
        "content": None,
        "sender_id": None,
        "sender_name": None,
        "timestamp": None
    }
    
    result = format_angelheart_message(invalid_message)
    print(f"无效消息格式化结果: {result}")
    assert "[格式化错误]" in result
    print("✓ 错误处理测试通过")
    
    print("\n✅ 所有 format_angelheart_message 测试通过！")


def test_format_unified_message():
    """测试现有格式化功能"""
    print("\n=== 测试 format_unified_message 函数 ===")
    
    # 测试标准 astrbot 上下文格式
    standard_message = {
        "role": "user",
        "content": "测试消息"
    }
    
    result = format_unified_message(standard_message)
    print(f"标准消息格式化结果: {result}")
    assert "[用户]" in result
    assert "测试消息" in result
    print("✓ 标准消息格式化测试通过")
    
    print("\n✅ 所有 format_unified_message 测试通过！")


if __name__ == "__main__":
    test_format_angelheart_message()
    test_format_unified_message()
    print("\n🎉 所有格式化测试完成！")