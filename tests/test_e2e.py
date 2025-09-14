"""
Angel Eye 插件 - 端到端测试
模拟插件在 AstrBot 环境中的完整调用流程。
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# 由于我们在 conftest.py 中已经处理了路径问题，可以直接导入
from ..main import AngelEyePlugin
from ..models.results import RetrieverResult, FilterResult


class TestEndToEnd:
    """测试插件的端到端流程"""

    @pytest.mark.asyncio
    async def test_plugin_pipeline_is_called_and_modifies_request(self):
        """测试插件主流程被调用，并且成功修改了 ProviderRequest"""
        # --- Arrange ---
        # 1. 模拟 AstrBot 上下文和 Provider
        mock_context = MagicMock()
        mock_small_model_provider = AsyncMock()
        mock_context.get_provider_by_id.return_value = mock_small_model_provider

        # 2. 模拟 LLM 响应
        mock_small_model_provider.text_chat.side_effect = [
            MagicMock(completion_text='{"should_search": true, "domain": "二次元", "search_query": "芙莉莲"}'),
            MagicMock(completion_text='{"selected_title": "芙莉莲"}'),
            MagicMock(completion_text="芙莉莲是主角。")
        ]

        # 3. 模拟 HTTP 客户端响应
        mock_html_content = """
        <html><body>
            <div id="mw-content-text">
                <div class="mw-parser-output">
                    <p>芙莉莲是《葬送的芙莉莲》的主角。</p>
                </div>
            </div>
        </body></html>
        """
        with patch("httpx.AsyncClient") as mock_http_client:
            mock_instance = AsyncMock()
            mock_http_client.return_value.__aenter__.return_value = mock_instance
            mock_instance.get = AsyncMock(return_value=MagicMock(
                raise_for_status=MagicMock(),
                text=mock_html_content
            ))

            # 4. 创建插件实例
            plugin = AngelEyePlugin(mock_context)

            # 5. 创建模拟的事件和请求对象
            mock_event = MagicMock()
            mock_request = MagicMock()
            mock_request.prompt = "芙莉莲是谁？"
            mock_request.system_prompt = "你是助手。"
            mock_request.contexts = []

            # --- Act ---
            # 调用插件的主钩子函数
            await plugin.run_knowledge_injection_pipeline(mock_event, mock_request)

            # --- Assert ---
            # 验证最终的 system_prompt 被修改
            expected_injection = "\n\n[补充知识]:\n---\n芙莉莲是主角。\n---\n"
            assert expected_injection in mock_request.system_prompt

            # 验证 prompt 也被修改以引导主模型
            assert "请参考系统提示词中提供的'补充知识'" in mock_request.prompt

            # 验证 LLM 被调用了 3 次 (Retriever, Filter, Summarizer)
            assert mock_small_model_provider.text_chat.await_count == 3

            # 验证 HTTP 客户端被调用了 2 次 (search, get_page_content)
            assert mock_instance.get.await_count == 2

    @pytest.mark.asyncio
    async def test_plugin_pipeline_stops_early_if_not_searching(self):
        """测试如果 Retriever 决定不搜索，插件流程会提前结束"""
        # --- Arrange ---
        mock_context = MagicMock()
        mock_small_model_provider = AsyncMock()
        mock_context.get_provider_by_id.return_value = mock_small_model_provider

        # 模拟 Retriever 决定不搜索
        mock_small_model_provider.text_chat.return_value = MagicMock(completion_text='{"should_search": false}')

        with patch("httpx.AsyncClient"):
            plugin = AngelEyePlugin(mock_context)

            mock_event = MagicMock()
            mock_request = MagicMock()
            mock_request.prompt = "你好，今天天气怎么样？"
            mock_request.system_prompt = "你是助手。"
            mock_request.contexts = []

            # --- Act ---
            await plugin.run_knowledge_injection_pipeline(mock_event, mock_request)

            # --- Assert ---
            # 验证 system_prompt 和 prompt 没有被修改
            assert mock_request.system_prompt == "你是助手。"
            assert mock_request.prompt == "你好，今天天气怎么样？"

            # 验证 LLM 只被调用了一次 (Retriever)
            assert mock_small_model_provider.text_chat.await_count == 1
