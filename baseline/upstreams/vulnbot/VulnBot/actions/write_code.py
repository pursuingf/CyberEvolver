
from pydantic import BaseModel, Field

from actions.execute_task import ExecuteTask
from prompts.prompt import DeepPentestPrompt
from server.chat.chat import _chat
from utils.log_common import build_logger

logger = build_logger()

class WriteCode(BaseModel):

    next_task: str

    action: str

    def run(self):

        logger.info(f"next_task: {self.next_task}")
        response, _ = _chat(query=DeepPentestPrompt.write_code.format(next_task=self.next_task))
        logger.info(f"LLM Response: {response}")

        code_executor = ExecuteTask(action=self.action, instruction=response, code=[])

        result = code_executor.run()
        return result
