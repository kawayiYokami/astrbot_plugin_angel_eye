"""
Angel Eye 插件 - 集成测试
验证插件核心模块间的协作。
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from ..roles.retriever import Retriever
from ..roles.filter import Filter
from ..roles.summarizer import Summarizer
from ..clients.moegirl_client import MoegirlClient
from ..models.results import RetrieverResult, FilterResult


class TestCorePipelineIntegration:
    """测试核心数据处理流水线的集成"""

    @pytest.mark.asyncio
    async def test_full_pipeline_success(self):
        """测试从检索到总结的完整成功流程"""
        # --- Arrange ---
        # 1. 模拟 LLM Provider 和 Prompt 加载
        with patch("astrbot.core.utils.io.read_file") as mock_read_file:
            mock_read_file.return_value = "Mock Prompt"
            mock_provider = AsyncMock()
            retriever = Retriever(mock_provider)
            filter_role = Filter(mock_provider)
            summarizer = Summarizer(mock_provider)

        # 2. 模拟 Retriever 的 LLM 响应
        mock_provider.text_chat.side_effect = [
            MagicMock(completion_text='{"should_search": true, "domain": "二次元", "search_query": "芙莉莲"}'),
            MagicMock(completion_text='{"selected_title": "芙莉莲"}'),
            MagicMock(completion_text="芙莉莲是一位长寿的精灵魔法使，故事的主角。")
        ]

        # 3. 模拟 MoegirlClient 的方法
        mock_moegirl_client = AsyncMock()
        mock_moegirl_client.search.return_value = [{"title": "芙莉莲", "url": "http://..."}]
        mock_moegirl_client.get_page_content.return_value = "芙莉莲是主角，一个精灵。"

        # 4. 准备输入数据
        contexts = [{"role": "user", "content": "你好"}]
        original_prompt = "芙莉莲是谁？"

        # --- Act ---
        # 1. Retriever 分析
        retriever_result = await retriever.analyze(contexts, original_prompt)

        # 2. Client 搜索 (集成测试中我们假设搜索成功)
        assert retriever_result.should_search is True
        search_results = await mock_moegirl_client.search(retriever_result.search_query)

        # 3. Filter 筛选
        selected_title = await filter_role.select_best_entry(search_results, original_prompt)

        # 4. Client 获取内容 (集成测试中我们假设获取成功)
        assert selected_title is not None
        full_content = await mock_moegirl_client.get_page_content(selected_title)

        # 5. Summarizer 总结
        summary = await summarizer.summarize(full_content, original_prompt)

        # --- Assert ---
        assert isinstance(retriever_result, RetrieverResult)
        assert retriever_result.domain == "二次元"
        assert len(search_results) == 1
        assert selected_title == "芙莉莲"
        assert full_content == "芙莉莲是主角，一个精灵。"
        assert summary == "芙莉莲是一位长寿的精灵魔法使，故事的主角。"

    @pytest.mark.asyncio
    async def test_pipeline_stops_if_retriever_says_no(self):
        """测试如果 Retriever 决定不搜索，流程应停止"""
        # --- Arrange ---
        with patch("astrbot.core.utils.io.read_file"):
            mock_provider = AsyncMock()
            retriever = Retriever(mock_provider)

        mock_provider.text_chat.return_value = MagicMock(completion_text='{"should_search": false}')

        contexts = []
        original_prompt = "今天天气怎么样？"

        # --- Act ---
        retriever_result = await retriever.analyze(contexts, original_prompt)

        # --- Assert ---
        assert isinstance(retriever_result, RetrieverResult)
        assert retriever_result.should_search is False
        # 验证没有进一步的调用
        mock_provider.text_chat.assert_awaited_once() # 只调用了 Retriever 的 analyze
