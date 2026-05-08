import uuid
from typing import List

from db.models.session_model import Session, SessionModel
from utils.session import with_session


@with_session
def add_session_to_db(session, session_data):

    new_task = SessionModel(
        id=uuid.uuid4().hex,
        name=session_data.name,
        current_role_name=session_data.current_role_name,
        init_description=session_data.init_description,
        current_planner_id=session_data.current_planner_id,
        history_planner_ids=','.join(session_data.history_planner_ids)
    )

    session.add(new_task)


@with_session
def fetch_all_sessions(session) -> List[Session]:

    result = session.query(SessionModel).all()

    result = [Session.model_validate(r) for r in result]

    return result
