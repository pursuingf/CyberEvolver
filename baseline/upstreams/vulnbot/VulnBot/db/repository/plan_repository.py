import uuid

from actions.planner import Planner
from db.models.plan_model import Plan, PlanModel
from utils.session import with_session


@with_session
def get_planner_by_id(session, planner_id: str):

    plan = session.query(PlanModel).filter(PlanModel.id == planner_id).first()
    plan = Plan.model_validate(plan)
    return plan


@with_session
def add_plan_to_db(session, plan: Plan):

    if not plan.id:
        plan.id = uuid.uuid4().hex
        new_plan = PlanModel(
            id=plan.id,
            goal=plan.goal,
            current_task_sequence=plan.current_task_sequence,
            plan_chat_id=plan.plan_chat_id,
            react_chat_id=plan.react_chat_id,
        )
        session.add(new_plan)

    return plan

