"""
数据模型
"""
from pydantic import BaseModel

class SearchResult(BaseModel):
    """萌娘百科搜索结果的数据模型"""
    title: str
    pageid: int
    snippet: str
    url: str