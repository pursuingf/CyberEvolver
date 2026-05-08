from sqlalchemy import Column, String, DateTime, func

from utils.session import Base


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String(32), primary_key=True)
    name = Column(String(50))
    chat_type = Column(String(50))
    create_time = Column(DateTime, default=func.now())