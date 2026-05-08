from typing import ClassVar


from prompts.scanner_prompt import ScannerPrompt
from roles.exploiter import Exploiter
from roles.role import Role
from utils.log_common import RoleType


class Scanner(Role):

    name: str = "Vulnerability Scanner"

    goal: str = (
        "Based on the reconnaissance results, "
        "further enumeration and check for vulnerabilities and misconfigurations in the target."
    )

    tools: str = (
        "Nikto, "
        "Curl, "
        "Dirb, "
        "Whatweb, "
        "WPScan, "
        "Sqlmap, "
        "ExploitDB, "
        "Wapiti, "
        "Aircrack-ng, "
        "Webshells, "
        "Weevely, " 
        "Tshark, "
        "Nmap(with NSE scripts)."
    )

    prompt: ClassVar[ScannerPrompt] = ScannerPrompt

    def __init__(self, console, max_interactions,  **kwargs):
        super().__init__(**kwargs)
        self.console = console
        self.max_interactions = max_interactions

    def put_message(self, message):
        super().put_message(self)
        if message.current_role_name == RoleType.SCANNER.value:
            message.current_role_name = RoleType.EXPLOITER.value
            message.history_planner_ids.append(self.planner.current_plan.id)
            message.current_planner_id = ''
            Exploiter(console=self.console, max_interactions=self.max_interactions).run(message)
