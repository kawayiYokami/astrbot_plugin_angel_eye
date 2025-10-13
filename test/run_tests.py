"""
运行所有测试的脚本
"""
import sys
import os
from test.test_formatter import test_format_angelheart_message, test_format_unified_message
from test.test_main import (
    test_get_dialogue_records_with_angelheart,
    test_get_dialogue_records_without_angelheart,
    test_error_handling
)

# 添加插件路径到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

print("=== 开始运行 Angel Eye 插件测试 ===\n")

# 运行格式化测试
print("1. 运行格式化功能测试...")
try:
    test_format_angelheart_message()
    test_format_unified_message()
    print("✅ 格式化功能测试通过！\n")
except Exception as e:
    print(f"❌ 格式化功能测试失败: {e}\n")

# 运行主模块测试
print("2. 运行主模块功能测试...")

try:
    test_get_dialogue_records_with_angelheart()
    test_get_dialogue_records_without_angelheart()
    test_error_handling()
    print("✅ 主模块功能测试通过！\n")
except Exception as e:
    print(f"❌ 主模块功能测试失败: {e}\n")

print("=== 测试完成 ===")
print("\n🎉 所有测试运行完毕！")