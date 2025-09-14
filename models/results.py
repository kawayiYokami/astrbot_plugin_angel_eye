"""
Angel Eye 插件 - 数据模型 (Results)
定义在各个处理阶段传递的数据结构。
"""
from dataclasses import dataclass
from typing import Optional

@dataclass
class RetrieverResult:
    """
    检索员角色的分析结果。
    """
    should_search: bool
    domain: Optional[str] = None
    search_query: Optional[str] = None

@dataclass
class FilterResult:
    """
    二次筛选角色的分析结果。
    """
    selected_title: Optional[str] = None

@dataclass
class SummaryResult:
    """
    整理员角色的分析结果。
    """
    summary_text: Optional[str] = None
