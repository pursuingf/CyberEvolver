from datetime import datetime
from typing import Optional
from pydantic import *
from langchain_core.documents import Document

from sqlalchemy import Column, Integer, String, DateTime, func

from utils.session import Base


class MatchDocument(Document):
    id: Optional[str] = None



class KnowledgeBaseModel(Base):

    __tablename__ = "knowledge_base"
    id = Column(Integer, primary_key=True, autoincrement=True, comment="知识库ID")
    kb_name = Column(String(50), comment="知识库名称")
    kb_info = Column(String(200), comment="知识库简介(用于Agent)")
    vs_type = Column(String(50), comment="向量库类型")
    embed_model = Column(String(50), comment="嵌入模型名称")
    file_count = Column(Integer, default=0, comment="文件数量")
    create_time = Column(DateTime, default=func.now(), comment="创建时间")

    def __repr__(self):
        return f"<KnowledgeBase(id='{self.id}', kb_name='{self.kb_name}',kb_intro='{self.kb_info} vs_type='{self.vs_type}', embed_model='{self.embed_model}', file_count='{self.file_count}', create_time='{self.create_time}')>"



class KnowledgeBaseSchema(BaseModel):
    id: int = Field(..., description="知识库ID")
    kb_name: str = Field(..., description="知识库名称")
    kb_info: Optional[str] = Field(None, description="知识库简介(用于Agent)")
    vs_type: Optional[str] = Field(None, description="向量库类型")
    embed_model: Optional[str] = Field(None, description="嵌入模型名称")
    file_count: Optional[int] = Field(0, description="文件数量")
    create_time: Optional[datetime] = Field(None, description="创建时间")


    class Config:
        from_attributes = True

