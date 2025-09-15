"""
缓存管理器的单元测试
测试核心缓存功能：存储、读取、键构建、统计信息等
"""

import asyncio
import sys
import os
import time
from typing import Dict, Any

# 将项目根目录添加到 Python 路径中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ..core.cache_manager import (
    get_knowledge,
    set_knowledge,
    build_doc_key,
    build_fact_key,
    get_cache_stats,
    reset_cache_stats,
    _cache  # 直接访问缓存对象以便清理测试数据
)


def test_basic_cache_operations():
    """测试基本的缓存存储和读取操作"""
    print("\n" + "="*60)
    print("测试：基本缓存操作")
    print("="*60)

    # 1. 测试存储和读取
    test_key = "test:basic:key"
    test_value = "这是一段测试内容，包含中文字符和 English text"

    print(f"\n1. 存储数据到缓存")
    print(f"   键: {test_key}")
    print(f"   值: {test_value}")
    set_knowledge(test_key, test_value)

    print(f"\n2. 从缓存读取数据")
    retrieved_value = get_knowledge(test_key)
    print(f"   读取的值: {retrieved_value}")

    assert retrieved_value == test_value, "缓存读取的值与存储的值不匹配"
    print("   ✓ 存储和读取测试通过")

    # 3. 测试不存在的键
    print(f"\n3. 测试读取不存在的键")
    non_existent = get_knowledge("non:existent:key")
    assert non_existent is None, "不存在的键应该返回 None"
    print(f"   读取不存在的键返回: {non_existent}")
    print("   ✓ 不存在键测试通过")

    # 清理测试数据
    _cache.delete(test_key)
    print("\n✅ 基本缓存操作测试完成")


def test_key_builders():
    """测试缓存键构建函数"""
    print("\n" + "="*60)
    print("测试：缓存键构建")
    print("="*60)

    # 1. 测试文档键构建
    print("\n1. 测试文档键构建")
    doc_key1 = build_doc_key("wikipedia", "长城")
    doc_key2 = build_doc_key("moegirl", "原神")

    print(f"   维基百科-长城: {doc_key1}")
    print(f"   萌娘百科-原神: {doc_key2}")

    assert doc_key1 == "doc:wikipedia:长城", "文档键格式不正确"
    assert doc_key2 == "doc:moegirl:原神", "文档键格式不正确"
    print("   ✓ 文档键构建测试通过")

    # 2. 测试事实键构建
    print("\n2. 测试事实键构建")
    fact_key1 = build_fact_key("纽约.坐标")
    fact_key2 = build_fact_key("朱祁镇.父亲")

    print(f"   纽约.坐标: {fact_key1}")
    print(f"   朱祁镇.父亲: {fact_key2}")

    assert fact_key1 == "fact:纽约.坐标", "事实键格式不正确"
    assert fact_key2 == "fact:朱祁镇.父亲", "事实键格式不正确"
    print("   ✓ 事实键构建测试通过")

    print("\n✅ 缓存键构建测试完成")


def test_cache_stats():
    """测试缓存统计功能"""
    print("\n" + "="*60)
    print("测试：缓存统计功能")
    print("="*60)

    # 1. 重置统计
    print("\n1. 重置统计信息")
    reset_cache_stats()
    stats = get_cache_stats()
    print(f"   初始统计: {stats}")
    assert stats["hits"] == 0, "初始命中数应为0"
    assert stats["misses"] == 0, "初始未命中数应为0"
    print("   ✓ 统计重置成功")

    # 2. 测试缓存命中
    print("\n2. 测试缓存命中统计")
    test_key = "test:stats:key"
    test_value = "统计测试数据"

    # 存储数据
    set_knowledge(test_key, test_value)

    # 第一次读取（缓存命中）
    _ = get_knowledge(test_key)
    stats = get_cache_stats()
    print(f"   第一次读取后: {stats}")
    assert stats["hits"] == 1, "命中数应为1"
    assert stats["misses"] == 0, "未命中数应为0"

    # 第二次读取（缓存命中）
    _ = get_knowledge(test_key)
    stats = get_cache_stats()
    print(f"   第二次读取后: {stats}")
    assert stats["hits"] == 2, "命中数应为2"

    # 3. 测试缓存未命中
    print("\n3. 测试缓存未命中统计")
    _ = get_knowledge("non:existent:key:for:stats")
    stats = get_cache_stats()
    print(f"   读取不存在的键后: {stats}")
    assert stats["misses"] == 1, "未命中数应为1"

    # 4. 测试命中率计算
    print("\n4. 测试命中率计算")
    hit_rate = stats["hit_rate"]
    expected_rate = 2 / 3  # 2次命中，1次未命中
    print(f"   命中率: {hit_rate:.2%}")
    print(f"   预期命中率: {expected_rate:.2%}")
    assert abs(hit_rate - expected_rate) < 0.01, "命中率计算不正确"
    print("   ✓ 命中率计算正确")

    # 清理测试数据
    _cache.delete(test_key)
    reset_cache_stats()

    print("\n✅ 缓存统计功能测试完成")


def test_large_content():
    """测试大容量内容的缓存"""
    print("\n" + "="*60)
    print("测试：大容量内容缓存")
    print("="*60)

    # 创建一个较大的内容（100KB）
    large_content = "这是一段很长的文本内容。" * 5000
    content_size = len(large_content.encode('utf-8'))

    print(f"\n1. 存储大容量内容")
    print(f"   内容大小: {content_size / 1024:.2f} KB")

    test_key = "test:large:content"
    set_knowledge(test_key, large_content)

    print(f"\n2. 读取大容量内容")
    retrieved = get_knowledge(test_key)

    assert retrieved == large_content, "大容量内容读取不正确"
    print(f"   ✓ 成功读取 {len(retrieved.encode('utf-8')) / 1024:.2f} KB 的内容")

    # 清理测试数据
    _cache.delete(test_key)

    print("\n✅ 大容量内容缓存测试完成")


def test_special_characters():
    """测试包含特殊字符的内容缓存"""
    print("\n" + "="*60)
    print("测试：特殊字符内容缓存")
    print("="*60)

    # 测试各种特殊字符
    test_cases = [
        ("test:emoji", "😊🎉🚀 表情符号测试"),
        ("test:symbols", "特殊符号: @#$%^&*()_+-=[]{}|;':\",./<>?"),
        ("test:unicode", "Unicode: αβγδ АБВГ 日本語 한국어"),
        ("test:newlines", "多行\n文本\r\n测试\n内容"),
        ("test:json", '{"key": "value", "number": 123, "nested": {"data": true}}'),
    ]

    for key, content in test_cases:
        print(f"\n测试: {key}")
        print(f"  存储内容: {content[:50]}...")

        set_knowledge(key, content)
        retrieved = get_knowledge(key)

        assert retrieved == content, f"特殊字符内容不匹配: {key}"
        print(f"  ✓ 成功存储和读取")

        # 清理
        _cache.delete(key)

    print("\n✅ 特殊字符内容缓存测试完成")


def test_concurrent_access():
    """测试并发访问缓存"""
    print("\n" + "="*60)
    print("测试：并发访问")
    print("="*60)

    import threading
    import random

    results = []
    errors = []

    def concurrent_operation(thread_id: int):
        """每个线程执行的操作"""
        try:
            # 写入操作
            key = f"test:concurrent:{thread_id}"
            value = f"线程 {thread_id} 的数据"
            set_knowledge(key, value)

            # 随机延迟
            time.sleep(random.uniform(0.01, 0.05))

            # 读取操作
            retrieved = get_knowledge(key)

            if retrieved == value:
                results.append(f"线程 {thread_id}: 成功")
            else:
                errors.append(f"线程 {thread_id}: 数据不匹配")

            # 清理
            _cache.delete(key)
        except Exception as e:
            errors.append(f"线程 {thread_id}: 错误 - {str(e)}")

    # 创建并启动多个线程
    threads = []
    thread_count = 10

    print(f"\n启动 {thread_count} 个并发线程...")

    for i in range(thread_count):
        thread = threading.Thread(target=concurrent_operation, args=(i,))
        threads.append(thread)
        thread.start()

    # 等待所有线程完成
    for thread in threads:
        thread.join()

    print(f"\n并发测试结果:")
    print(f"  成功: {len(results)} 个线程")
    print(f"  错误: {len(errors)} 个线程")

    if errors:
        print("\n错误详情:")
        for error in errors:
            print(f"  - {error}")

    assert len(errors) == 0, "并发访问出现错误"
    print("\n✅ 并发访问测试完成")


def run_all_tests():
    """运行所有测试"""
    print("\n" + "="*80)
    print("开始缓存管理器完整测试")
    print("="*80)

    test_functions = [
        ("基本缓存操作", test_basic_cache_operations),
        ("缓存键构建", test_key_builders),
        ("缓存统计功能", test_cache_stats),
        ("大容量内容", test_large_content),
        ("特殊字符内容", test_special_characters),
        ("并发访问", test_concurrent_access),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in test_functions:
        try:
            test_func()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n❌ {test_name} 测试失败:")
            print(f"   错误: {str(e)}")
            import traceback
            traceback.print_exc()

    # 最终统计
    print("\n" + "="*80)
    print("测试总结")
    print("="*80)
    print(f"总测试数: {len(test_functions)}")
    print(f"✅ 通过: {passed}")
    if failed > 0:
        print(f"❌ 失败: {failed}")

    # 显示缓存最终状态
    final_stats = get_cache_stats()
    print(f"\n最终缓存统计:")
    print(f"  命中: {final_stats['hits']}")
    print(f"  未命中: {final_stats['misses']}")
    print(f"  命中率: {final_stats['hit_rate']:.2%}")

    print("\n" + "="*80)
    if failed == 0:
        print("🎉 所有测试通过！缓存管理器工作正常")
    else:
        print(f"⚠️ 有 {failed} 个测试失败，需要检查")
    print("="*80)

    return failed == 0


if __name__ == "__main__":
    # 运行所有测试
    success = run_all_tests()

    # 设置退出码
    exit(0 if success else 1)