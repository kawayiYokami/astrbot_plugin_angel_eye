"""
知识结果数据模型
定义知识获取后返回的数据格式
"""
from typing import List, Optional
from pydantic import BaseModel, Field


class KnowledgeChunk(BaseModel):
    """
    单个知识片段模型
    表示从某个数据源获取的一段知识内容
    """
    source: str = Field(
        description="知识来源，如 'wikipedia', 'moegirl', 'wikidata'"
    )
    entity: str = Field(
        description="关联的实体名称"
    )
    content: str = Field(
        description="知识内容文本"
    )
    source_url: Optional[str] = Field(
        default=None,
        description="原始来源URL（如果有）"
    )


class KnowledgeResult(BaseModel):
    """
    知识获取结果的聚合模型
    包含所有获取到的知识片段
    """
    chunks: List[KnowledgeChunk] = Field(
        default_factory=list,
        description="所有获取到的知识片段列表"
    )

    def to_context_string(self) -> str:
        """
        将所有知识片段格式化为可注入上下文的字符串
        """
        if not self.chunks:
            return ""

        parts = []
        for chunk in self.chunks:
            header = f"【{chunk.entity}】"
            if chunk.source_url:
                header += f" (来源: {chunk.source})"
            parts.append(f"{header}\n{chunk.content}")

        return "\n\n".join(parts)