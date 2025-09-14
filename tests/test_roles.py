"""
Angel Eye 插件 - Roles 模块单元测试
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import json
from ..roles.retriever import Retriever
from ..roles.filter import Filter
from ..roles.summarizer import Summarizer
from ..models.results import RetrieverResult, FilterResult

# --- Fixtures for Roles ---

@pytest.fixture
def mock_provider():
    """提供一个模拟的 LLM Provider"""
    return AsyncMock()

@pytest.fixture
def retriever(mock_provider):
    """提供一个配置了模拟 Provider 的 Retriever 实例"""
    with patch("astrbot.core.utils.io.read_file", return_value="Mock Retriever Prompt: {dialogue}"):
        return Retriever(mock_provider)

@pytest.fixture
def filter_role(mock_provider):
    """提供一个配置了模拟 Provider 的 Filter 实例"""
    with patch("astrbot.core.utils.io.read_file", return_value="Mock Filter Prompt: {original_prompt}, {search_results}"):
        return Filter(mock_provider)

@pytest.fixture
def summarizer(mock_provider):
    """提供一个配置了模拟 Provider 的 Summarizer 实例"""
    with patch("astrbot.core.utils.io.read_file", return_value="Mock Summarizer Prompt: {original_prompt}, {full_content}"):
        return Summarizer(mock_provider)

# --- Tests for Retriever ---

class TestRetriever:
    """测试 Retriever 角色"""

    @pytest.mark.asyncio
    async def test_analyze_should_search_true(self, retriever, mock_provider):
        """测试 analyze 方法在 LLM 建议搜索时的正确解析"""
        # Arrange
        contexts = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好！"}]
        current_prompt = "芙莉莲是谁？"
        mock_llm_response_text = '{"should_search": true, "domain": "二次元", "search_query": "芙莉莲"}'
        mock_provider.text_chat.return_value = MagicMock(completion_text=mock_llm_response_text)

        # Act
        result = await retriever.analyze(contexts, current_prompt)

        # Assert
        assert isinstance(result, RetrieverResult)
        assert result.should_search is True
        assert result.domain == "二次元"
        assert result.search_query == "芙莉莲"
        mock_provider.text_chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_analyze_should_search_false(self, retriever, mock_provider):
        """测试 analyze 方法在 LLM 建议不搜索时的正确解析"""
        # Arrange
        contexts = []
        current_prompt = "今天天气怎么样？"
        mock_llm_response_text = '{"should_search": false}'
        mock_provider.text_chat.return_value = MagicMock(completion_text=mock_llm_response_text)

        # Act
        result = await retriever.analyze(contexts, current_prompt)

        # Assert
        assert isinstance(result, RetrieverResult)
        assert result.should_search is False
        assert result.domain is None
        assert result.search_query is None

    @pytest.mark.asyncio
    async def test_analyze_invalid_json(self, retriever, mock_provider):
        """测试 analyze 方法在 LLM 返回无效 JSON 时的处理"""
        # Arrange
        contexts = []
        current_prompt = "芙莉莲是谁？"
        mock_llm_response_text = '这不是JSON'
        mock_provider.text_chat.return_value = MagicMock(completion_text=mock_llm_response_text)

        # Act
        result = await retriever.analyze(contexts, current_prompt)

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_analyze_provider_exception(self, retriever, mock_provider):
        """测试 analyze 方法在 LLM Provider 抛出异常时的处理"""
        # Arrange
        contexts = []
        current_prompt = "芙莉莲是谁？"
        mock_provider.text_chat.side_effect = Exception("网络错误")

        # Act
        result = await retriever.analyze(contexts, current_prompt)

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_analyze_no_provider(self):
        """测试 analyze 方法在没有 Provider 时的处理"""
        # Arrange
        with patch("astrbot.core.utils.io.read_file", return_value="Mock Prompt"):
            retriever_no_provider = Retriever(None)
        contexts = []
        current_prompt = "芙莉莲是谁？"

        # Act
        result = await retriever_no_provider.analyze(contexts, current_prompt)

        # Assert
        assert result is None

# --- Tests for Filter ---

class TestFilter:
    """测试 Filter 角色"""

    @pytest.mark.asyncio
    async def test_select_best_entry_success(self, filter_role, mock_provider):
        """测试 select_best_entry 方法成功选择条目"""
        # Arrange
        search_results = [{"title": "芙莉莲"}, {"title": "葬送的芙莉莲"}]
        original_prompt = "芙莉莲是谁？"
        mock_llm_response_text = '{"selected_title": "芙莉莲"}'
        mock_provider.text_chat.return_value = MagicMock(completion_text=mock_llm_response_text)

        # Act
        selected_title = await filter_role.select_best_entry(search_results, original_prompt)

        # Assert
        assert selected_title == "芙莉莲"
        mock_provider.text_chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_select_best_entry_none_selected(self, filter_role, mock_provider):
        """测试 select_best_entry 方法在未选择条目时返回 None"""
        # Arrange
        search_results = [{"title": "芙莉莲"}]
        original_prompt = "芙莉莲是谁？"
        mock_llm_response_text = '{"selected_title": null}'
        mock_provider.text_chat.return_value = MagicMock(completion_text=mock_llm_response_text)

        # Act
        selected_title = await filter_role.select_best_entry(search_results, original_prompt)

        # Assert
        assert selected_title is None

    @pytest.mark.asyncio
    async def test_select_best_entry_invalid_json(self, filter_role, mock_provider):
        """测试 select_best_entry 方法在 LLM 返回无效 JSON 时的处理"""
        # Arrange
        search_results = [{"title": "芙莉莲"}]
        original_prompt = "芙莉莲是谁？"
        mock_llm_response_text = '无效的响应'
        mock_provider.text_chat.return_value = MagicMock(completion_text=mock_llm_response_text)

        # Act
        selected_title = await filter_role.select_best_entry(search_results, original_prompt)

        # Assert
        assert selected_title is None

    @pytest.mark.asyncio
    async def test_select_best_entry_provider_exception(self, filter_role, mock_provider):
        """测试 select_best_entry 方法在 LLM Provider 抛出异常时的处理"""
        # Arrange
        search_results = [{"title": "芙莉莲"}]
        original_prompt = "芙莉莲是谁？"
        mock_provider.text_chat.side_effect = Exception("API 错误")

        # Act
        selected_title = await filter_role.select_best_entry(search_results, original_prompt)

        # Assert
        assert selected_title is None

    @pytest.mark.asyncio
    async def test_select_best_entry_no_provider(self):
        """测试 select_best_entry 方法在没有 Provider 时的处理"""
        # Arrange
        with patch("astrbot.core.utils.io.read_file", return_value="Mock Prompt"):
            filter_no_provider = Filter(None)
        search_results = [{"title": "芙莉莲"}]
        original_prompt = "芙莉莲是谁？"

        # Act
        selected_title = await filter_no_provider.select_best_entry(search_results, original_prompt)

        # Assert
        assert selected_title is None

# --- Tests for Summarizer ---

class TestSummarizer:
    """测试 Summarizer 角色"""

    @pytest.mark.asyncio
    async def test_summarize_success(self, summarizer, mock_provider):
        """测试 summarize 方法成功生成摘要"""
        # Arrange
        full_content = "芙莉莲是一位长寿的精灵魔法使..."
        original_prompt = "芙莉莲是谁？"
        mock_summary = "芙莉莲是主角，一个精灵。"
        mock_provider.text_chat.return_value = MagicMock(completion_text=mock_summary)

        # Act
        summary = await summarizer.summarize(full_content, original_prompt)

        # Assert
        assert summary == mock_summary
        mock_provider.text_chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_summarize_provider_exception(self, summarizer, mock_provider):
        """测试 summarize 方法在 LLM Provider 抛出异常时的处理"""
        # Arrange
        full_content = "芙莉莲是一位长寿的精灵魔法使..."
        original_prompt = "芙莉莲是谁？"
        mock_provider.text_chat.side_effect = Exception("超时")

        # Act
        summary = await summarizer.summarize(full_content, original_prompt)

        # Assert
        assert summary is None

    @pytest.mark.asyncio
    async def test_summarize_no_provider(self):
        """测试 summarize 方法在没有 Provider 时的处理"""
        # Arrange
        with patch("astrbot.core.utils.io.read_file", return_value="Mock Prompt"):
            summarizer_no_provider = Summarizer(None)
        full_content = "芙莉莲是一位长寿的精灵魔法使..."
        original_prompt = "芙莉莲是谁？"

        # Act
        summary = await summarizer_no_provider.summarize(full_content, original_prompt)

        # Assert
        assert summary is None
