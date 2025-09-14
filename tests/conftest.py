"""
Angel Eye 插件 - Pytest 配置和共享 Fixtures
"""
import sys
import os
from unittest.mock import AsyncMock, MagicMock

# 为了能导入上游的 AstrBot 类，我们需要将项目根目录添加到 Python 路径中
# 这在运行测试时是必要的，因为我们是在插件目录下运行 pytest
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, project_root)

# --- Mocks for AstrBot Core Dependencies ---

# 模拟 Provider (LLM)
class MockProvider:
    def __init__(self):
        self.text_chat = AsyncMock()

# 模拟 AstrMessageEvent
class MockAstrMessageEvent:
    def __init__(self):
        self.unified_msg_origin = "test_chat_id"
        self.message_str = "Test message"
        self.is_at_or_wake_command = False

# 模拟 ProviderRequest
class MockProviderRequest:
    def __init__(self):
        self.prompt = "Initial prompt"
        self.system_prompt = "System prompt"
        self.contexts = []

# 模拟 LLMResponse
class MockLLMResponse:
    def __init__(self, completion_text=""):
        self.completion_text = completion_text

# --- Pytest Fixtures ---

import pytest

@pytest.fixture
def mock_provider():
    """提供一个模拟的 LLM Provider"""
    return MockProvider()

@pytest.fixture
def mock_astr_message_event():
    """提供一个模拟的 AstrMessageEvent"""
    return MockAstrMessageEvent()

@pytest.fixture
def mock_provider_request():
    """提供一个模拟的 ProviderRequest"""
    return MockProviderRequest()

@pytest.fixture
def mock_llm_response():
    """提供一个模拟的 LLMResponse"""
    return MockLLMResponse()
