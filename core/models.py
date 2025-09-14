"""
萌娘百科搜索结果的数据模型
"""

from dataclasses import dataclass

@dataclass
class SearchResult:
    """萌娘百科搜索结果的数据模型"""
    title: str      # 词条标题
    pageid: int     # 页面ID
    snippet: str    # 结果摘要 (HTML格式)
    url: str        # 词条的完整URL