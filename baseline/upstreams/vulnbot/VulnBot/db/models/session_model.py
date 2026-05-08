from typing import List

from pydantic import BaseModel, Field
from pydantic.class_validators import validator
from sqlalchemy import Column, String

from utils.session import Base


class SessionModel(Base):
    __tablename__ = "sessions"
    id = Column(String(32), primary_key=True)
    name = Column(String(50))
    init_description = Column(String(512))
    current_role_name = Column(String(50))
    current_planner_id = Column(String(32), index=True)
    history_planner_ids = Column(String(256))


class ArrayField(List):
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        return handler(List)

class Session(BaseModel):
    id: str = Field(None)
    name: str = Field(None)
    init_description: str = Field(None)
    current_role_name: str = Field(None)
    current_planner_id: str = Field(None)
    history_planner_ids: ArrayField[str] = Field(default_factory=list)

    @validator('history_planner_ids', pre=True, each_item=False)
    def parse_history_planner_ids(cls, value):
        if isinstance(value, str):
            return value.split(',') if value else []
        return value

    class Config:
        from_attributes = True