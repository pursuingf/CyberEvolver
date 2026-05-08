from collections import deque
from typing import List, Optional, Dict

from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Integer
from sqlalchemy.orm import relationship

from db.models.task_model import Task
from utils.session import Base


class PlanModel(Base):
    __tablename__ = "plans"
    id = Column(String(32), primary_key=True)
    goal = Column(String(512))
    current_task_sequence = Column(Integer)
    plan_chat_id = Column(String(32))
    react_chat_id = Column(String(32))

    tasks = relationship("TaskModel", back_populates="plan", order_by="TaskModel.sequence")


class Plan(BaseModel):
    id: str = Field(None)
    goal: str = Field(None)
    current_task_sequence: int = Field(0)
    plan_chat_id: str = Field(None)
    react_chat_id: str = Field(None)
    tasks: List[Task] = []

    class Config:
        from_attributes = True

    def get_sorted_tasks(self) -> List[Task]:
        task_map = {task.sequence: task for task in self.tasks}

        graph: Dict[int, List[int]] = {task.sequence: [] for task in self.tasks}
        for task in self.tasks:
            for dep in task.dependencies:
                graph[dep].append(task.sequence)

        in_degree: Dict[int, int] = {task.sequence: len(task.dependencies) for task in self.tasks}

        sorted_tasks = []
        queue = deque([task.sequence for task in self.tasks if not task.dependencies])

        while queue:
            current_sequence = queue.popleft()
            current_task = task_map[current_sequence]
            sorted_tasks.append(current_task)

            for next_sequence in graph[current_sequence]:
                in_degree[next_sequence] -= 1
                if in_degree[next_sequence] == 0:
                    queue.append(next_sequence)

        if len(sorted_tasks) != len(self.tasks):
            raise ValueError("Tasks have cyclic dependencies")

        return sorted_tasks

    @property
    def current_task(self) -> Optional[Task]:
        sorted_tasks = self.get_sorted_tasks()
        if not sorted_tasks:
            return None

        for task in sorted_tasks:
            if not task.is_finished:
                return task
        return None


    @property
    def finished_tasks(self):
        sorted_tasks = self.get_sorted_tasks()
        return [task for task in sorted_tasks if task.is_finished and task.is_success]

    @property
    def finished_success_tasks(self):
        sorted_tasks = self.get_sorted_tasks()
        return [task.instruction for task in sorted_tasks if task.is_finished and task.is_success]

    @property
    def finished_fail_tasks(self):
        sorted_tasks = self.get_sorted_tasks()
        return [task.instruction for task in sorted_tasks if task.is_finished and not task.is_success]
