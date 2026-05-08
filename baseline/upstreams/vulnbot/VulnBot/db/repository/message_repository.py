import uuid
from typing import Dict, List

from db.models.message_model import Message, MessageModel
from utils.session import with_session


@with_session
def add_message_to_db(
        session,
        conversation_id: str,
        chat_type,
        query,
        response="",
        message_id=None,
        metadata: Dict = {},
):
    if not message_id:
        message_id = uuid.uuid4().hex
    m = MessageModel(
        id=message_id,
        chat_type=chat_type,
        query=query,
        response=response,
        conversation_id=conversation_id,
        meta_data=metadata,
    )
    session.add(m)
    return m.id


@with_session
def get_conversation_messages(session, conversation_id) -> List[Message]:

    messages = session.query(MessageModel).filter_by(conversation_id=conversation_id).order_by(MessageModel.create_time).all()

    messages = [Message.model_validate(m) for m in messages]

    return messages
