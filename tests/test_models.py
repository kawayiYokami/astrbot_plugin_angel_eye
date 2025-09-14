"""
Angel Eye 插件 - Models 模块单元测试
"""
import pytest
from ..models.results import RetrieverResult, FilterResult

class TestModels:
    """测试数据模型类"""

    def test_retriever_result_initialization(self):
        """测试 RetrieverResult 的初始化"""
        # Arrange
        should_search = True
        domain = "二次元"
        search_query = "芙莉莲"

        # Act
        result = RetrieverResult(should_search, domain, search_query)

        # Assert
        assert result.should_search == should_search
        assert result.domain == domain
        assert result.search_query == search_query

    def test_retriever_result_initialization_with_defaults(self):
        """测试 RetrieverResult 使用默认值初始化"""
        # Arrange & Act
        result = RetrieverResult(should_search=False)

        # Assert
        assert result.should_search == False
        assert result.domain is None
        assert result.search_query is None

    def test_filter_result_initialization(self):
        """测试 FilterResult 的初始化"""
        # Arrange
        selected_title = "芙莉莲"

        # Act
        result = FilterResult(selected_title)

        # Assert
        assert result.selected_title == selected_title

    def test_filter_result_initialization_with_none(self):
        """测试 FilterResult 使用 None 初始化"""
        # Arrange & Act
        result = FilterResult(None)

        # Assert
        assert result.selected_title is None
