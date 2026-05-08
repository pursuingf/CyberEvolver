from typing import ClassVar

from prompts.collector_prompt import CollectorPrompt
from roles.role import Role
from roles.scanner import Scanner
from utils.log_common import RoleType


class Collector(Role):
    name: str = "Information Collection"

    goal: str = (
        "Perform a full scan of the target to identify all open ports and services."
    )

    tools: str = (
        "Nmap, "
        "Curl, "
        "Wget, "
        "Tcpdump, "
        "Whois, "
        "Dmitry, "
        "Dnsenum, "
        "Netdiscover, "
        "Amap, "
        "Enum4linux, "
        "Smbclient, "
        "Amass, "
        "SSLscan, "
        "SpiderFoot, "
        "Fierce."
    )

    prompt: ClassVar[CollectorPrompt] = CollectorPrompt

    def __init__(self, console, max_interactions, **kwargs):
        super().__init__(**kwargs)
        self.console = console
        self.max_interactions = max_interactions

    def put_message(self, message):
        super().put_message(self)
        if message.current_role_name == RoleType.COLLECTOR.value:
            message.current_role_name = RoleType.SCANNER.value
            message.history_planner_ids.append(self.planner.current_plan.id)
            message.current_planner_id = ''
            Scanner(console=self.console, max_interactions=self.max_interactions).run(message)
