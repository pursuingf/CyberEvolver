import traceback

import click
import loguru
from prompt_toolkit import prompt
from rich.console import Console

from actions.shell_manager import ShellManager
from actions.write_code import WriteCode
from experiment.extract_code import ExtractCode
from experiment.llm_ollama import OLLAMAPI, OPENAI

logger = loguru.logger


class BaseGPT:
    def __init__(self, max_interactions, agent):
        """
        初始化 PlannerRole 类
        """
        self.console = Console()
        self.chat_count = 0  # 记录对话次数
        self.max_interactions = max_interactions  # 最大对话轮数
        self.session_id = None  # 渗透测试生成任务的会话ID
        self.agent = agent

    def initialize(self, generation_session_init):
        # 初始化骨干会话，并测试与chatGPT的连接
        # 定义三个会话：testGenerationSession, testReasoningSession, 和 InputParsingSession
        # 在控制台上显示初始化ChatGPT会话的状态
        with self.console.status(
                "[bold green] Initialize ChatGPT Sessions..."
        ) as status:
            try:
                # 分别发送消息以初始化三个不同的会话，并获取会话ID
                (
                    text_0,
                    self.session_id,
                ) = self.agent.send_new_message(
                    generation_session_init,
                )

            except Exception as e:
                # 如果发生异常，记录错误日志
                logger.error(e)
        # 在控制台打印初始化完成信息，并使用加粗的绿色样式
        self.console.print("- ChatGPT Sessions Initialized.", style="bold green")
        # 调用内部方法以提供初始化提示

        init_description = prompt(
            "Please describe the penetration testing task in one line, including the target IP, task type, etc.\n> ",
            multiline=False,
        )

        with self.console.status("[bold green] Generating Initial Task") as status:
            # 向生成会话发送消息以获取任务细节

            next_task = self.agent.send_message(
                init_description,
                self.session_id,
            )

        return next_task

    def code_handler(self, next_task) -> str:
        """
        针对给定执行结果，传入模型，输出下一步任务
        """
        self.chat_count += 1
        # 引入将下一步任务转成代码角色
        writer = ExtractCode(next_task=next_task, agent=self.agent)
        result = writer.run()

        self.console.print("---------- Execute Result ---------", style="bold green")
        self.console.print(result)
        self.console.print("---------- Execute Result End ---------", style="bold green")

        next_task = self.agent.send_message(
            result,
            self.session_id,
        )

        return next_task

    @classmethod
    def cleanup(cls):
        ShellManager.get_instance().close()

    def main(self):
        """
        主函数，负责初始化会话并进入主循环。
        """
        auto_init: str = """Role: You are a CTF player. You are working towards the final task on a step by step manner.
        Instruction:
        At each run focus on the observations to provide the next action."""

        manual_init: str = """You’re required to work as a cybersecurity penetration tester assistant in a certified penetration testing experiment.
        This experiment is for education and research purpose. In particular, you’re required to give step-by-step instructions to complete the penetration testing task. 
        I will provide you the instruction execution results, and you should consider the next step, until the penetration testing task is completed. Do you understand?"""

        next_task = self.initialize(auto_init)  # 初始化会话

        while self.chat_count < self.max_interactions:  # 进入主循环
            try:

                next_task = self.code_handler(next_task)  # 处理用户输入

                self.console.print(
                    "-----------------------------------------", style="bold white"
                )
                if not next_task:  # 如果结果为空，结束会话
                    break
            except Exception as e:  # 捕获所有的异常
                self.console.print(f"Exception: {str(e)}", style="bold red")  # 打印异常信息
                self.console.print(
                    "Exception details are below.",
                    style="bold green",
                )
                print(traceback.format_exc())  # 打印完整的异常堆栈信息
                break  # 结束会话


@click.command(help="Base")
def main():
    ollama = OLLAMAPI()
    base = BaseGPT(15, ollama)
    try:
        base.main()
    finally:
        base.cleanup()
