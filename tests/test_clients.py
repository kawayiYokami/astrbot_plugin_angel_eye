"""
Angel Eye 插件 - Clients 模块单元测试
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
from ..clients.moegirl_client import MoegirlClient
from ..clients.general_client import GeneralClient

class TestMoegirlClient:
    """测试 MoegirlClient"""

    @pytest.fixture
    def client(self):
        """为每个测试提供一个 MoegirlClient 实例"""
        return MoegirlClient()

    @pytest.mark.asyncio
    async def test_search_success(self, client):
        """测试 search 方法成功返回结果"""
        # Arrange
        query = "芙莉莲"
        mock_response_data = {
            "query": {
                "search": [
                    {
                        "title": "芙莉莲",
                        "pageid": 12345
                    },
                    {
                        "title": "葬送的芙莉莲",
                        "pageid": 67890
                    }
                ]
            }
        }

        # 使用 patch 模拟 httpx.AsyncClient
        with patch("httpx.AsyncClient") as mock_http_client:
            # 配置 mock 实例
            mock_instance = AsyncMock()
            mock_http_client.return_value.__aenter__.return_value = mock_instance

            # 配置 get 方法的返回值
            mock_instance.get = AsyncMock(return_value=MagicMock(
                raise_for_status=MagicMock(),
                json=MagicMock(return_value=mock_response_data)
            ))

            # Act
            results = await client.search(query)

            # Assert
            assert len(results) == 2
            assert results[0]["title"] == "芙莉莲"
            assert results[0]["url"] == "https://zh.moegirl.org.cn/index.php?curid=12345"
            assert results[1]["title"] == "葬送的芙莉莲"
            assert results[1]["url"] == "https://zh.moegirl.org.cn/index.php?curid=67890"

    @pytest.mark.asyncio
    async def test_search_no_results(self, client):
        """测试 search 方法在无结果时返回空列表"""
        # Arrange
        query = "一个不存在的词条"
        mock_response_data = {"query": {"search": []}}

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_instance = AsyncMock()
            mock_http_client.return_value.__aenter__.return_value = mock_instance
            mock_instance.get = AsyncMock(return_value=MagicMock(
                raise_for_status=MagicMock(),
                json=MagicMock(return_value=mock_response_data)
            ))

            # Act
            results = await client.search(query)

            # Assert
            assert results == []

    @pytest.mark.asyncio
    async def test_search_http_error(self, client):
        """测试 search 方法在 HTTP 错误时的处理"""
        # Arrange
        query = "芙莉莲"

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_instance = AsyncMock()
            mock_http_client.return_value.__aenter__.return_value = mock_instance
            # 配置 get 方法抛出 HTTPStatusError
            mock_instance.get = AsyncMock(side_effect=httpx.HTTPStatusError(
                "Bad Request", request=MagicMock(), response=MagicMock(status_code=400)
            ))

            # Act
            results = await client.search(query)

            # Assert
            assert results == [] # 应该返回空列表

    @pytest.mark.asyncio
    async def test_get_page_content_success(self, client):
        """测试 get_page_content 方法成功返回内容"""
        # Arrange
        title = "芙莉莲"
        mock_html_content = """
        <html>
        <body>
            <div id="mw-content-text">
                <div class="mw-parser-output">
                    <p>这是芙莉莲的介绍。</p>
                    <p>她是一位精灵魔法使。</p>
                </div>
            </div>
        </body>
        </html>
        """

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_instance = AsyncMock()
            mock_http_client.return_value.__aenter__.return_value = mock_instance
            mock_instance.get = AsyncMock(return_value=MagicMock(
                raise_for_status=MagicMock(),
                text=mock_html_content
            ))

            # Act
            content = await client.get_page_content(title)

            # Assert
            expected_content = "这是芙莉莲的介绍。\n她是一位精灵魔法使。"
            assert content == expected_content

    @pytest.mark.asyncio
    async def test_get_page_content_http_error(self, client):
        """测试 get_page_content 方法在 HTTP 错误时的处理"""
        # Arrange
        title = "芙莉莲"

        with patch("httpx.AsyncClient") as mock_http_client:
            mock_instance = AsyncMock()
            mock_http_client.return_value.__aenter__.return_value = mock_instance
            mock_instance.get = AsyncMock(side_effect=httpx.HTTPStatusError(
                "Not Found", request=MagicMock(), response=MagicMock(status_code=404)
            ))

            # Act
            content = await client.get_page_content(title)

            # Assert
            assert content is None # 应该返回 None

class TestGeneralClient:
    """测试 GeneralClient"""

    @pytest.fixture
    def client(self):
        """为每个测试提供一个 GeneralClient 实例"""
        return GeneralClient()

    @pytest.mark.asyncio
    async def test_search_returns_empty_list(self, client):
        """测试 GeneralClient 的 search 方法总是返回空列表"""
        # Arrange
        query = "任何查询"

        # Act
        results = await client.search(query)

        # Assert
        assert results == []

    @pytest.mark.asyncio
    async def test_get_page_content_returns_none(self, client):
        """测试 GeneralClient 的 get_page_content 方法总是返回 None"""
        # Arrange
        title = "任何标题"

        # Act
        content = await client.get_page_content(title)

        # Assert
        assert content is None
