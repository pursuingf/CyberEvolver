import uuid
from typing import List

from db.models.task_model import TaskModel, Task
from utils.session import with_session


@with_session
def add_task_to_plan(session, tasks: List[Task]):
    """Add a new task to a plan"""
    new_tasks = []
    for task in tasks:
        new_task = TaskModel(id=uuid.uuid4().hex,
                             plan_id=task.plan_id,
                             sequence=task.sequence,
                             action=task.action,
                             instruction=task.instruction,
                             code=task.code,
                             dependencies=task.dependencies,
                             is_finished=task.is_finished,
                             is_success=task.is_success,
                             result=task.result[:8192])
        new_tasks.append(new_task)

    session.add_all(new_tasks)
