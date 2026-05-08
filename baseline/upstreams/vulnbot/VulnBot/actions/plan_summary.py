from typing import List, Optional
from pydantic import BaseModel, Field

from db.repository.plan_repository import get_planner_by_id
from prompts.prompt import DeepPentestPrompt
from server.chat.chat import _chat
from utils.log_common import build_logger

logger = build_logger()


class PlannerSummary(BaseModel):
    history_planner_ids: List[str] = Field(default_factory=list)

    def get_summary(self):
        if len(self.history_planner_ids) == 0:
            return ""

        summary = "**Previous Phase**:\n"
        for index, planner_id in enumerate(self.history_planner_ids):
            plan = get_planner_by_id(planner_id)
            for task in plan.finished_tasks:
                summary += (f"**Instruction**: {task.instruction}\n, **Code**: {task.code}\n, **Result**: {task.result}\n"
                            f"------\n")

        response, _ = _chat(query=DeepPentestPrompt.write_summary + str(summary), summary=False)

        logger.info(f"summary: {response}")

        return response
