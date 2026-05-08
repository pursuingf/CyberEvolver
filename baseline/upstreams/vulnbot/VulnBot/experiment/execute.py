import re
import time
from typing import List
import paramiko
from click import prompt
from pydantic import BaseModel

from actions.run_code import RunCode
from actions.shell_manager import ShellManager

from utils.log_common import build_logger
from prompt_toolkit import prompt

logger = build_logger()


class Execute(BaseModel):
    instruction: str
    code: List[str]

    def parse_response(self) -> list[str]:

        initial_matches = re.findall(
            r'<execute>\s*(.*?)\s*</execute>', self.instruction, re.DOTALL
        )

        cleaned_matches = []
        for match in initial_matches:

            if '<execute>' in match:
                inner_match = re.search(r'<execute>\s*(.*?)$', match)
                if inner_match:
                    cleaned_matches.append(inner_match.group(1).strip())
            else:
                cleaned_matches.append(match.strip())

        return cleaned_matches

    def run(self) -> str:
        thought = self.parse_response()
        self.code = thought

        logger.info(f"Running {thought}")

        shell = ShellManager.get_instance().get_shell()

        result = ""
        try:
            SMB_PROMPTS = [
                'command not found',
                '?Invalid command.'
            ]

            PASSWORD_PROMPTS = [
                'password:',
                'Password for'
                '[sudo] password for',
            ]

            skip_next = False

            for i, command in enumerate(self.code):
                # 添加跳过标记
                if skip_next:
                    skip_next = False
                    continue

                result += f'Action:{command}\nObservation: '
                output = shell.execute_cmd(command)
                result += output + '\n'
                out_line = output.strip().split('\n')

                last_line = out_line[-1]

                if any(prompt in last_line for prompt in PASSWORD_PROMPTS):
                    if i + 1 < len(self.code):
                        result += f'Action:{self.code[i + 1]}\nObservation: '
                        next_output = shell.execute_cmd(self.code[i + 1])
                        result += next_output + '\n'
                        skip_next = True
                        if any(prompt in next_output.strip().split('\n')[-1] for prompt in PASSWORD_PROMPTS):
                            shell.shell.send('\x03')  # Send Ctrl+C
                            time.sleep(0.5)  # Wait for Ctrl+C to take effect
                            # Clear the previous result
                            result = result.rsplit('Action:', 1)[0] + f'Action:{self.code[i + 1]}\nObservation: '
                            # Resend the second command
                            new_output = shell.execute_cmd(self.code[i + 1])
                            result += new_output + '\n'
                    else:
                        shell.shell.send('\x03')  # Send Ctrl+C for single command case

                if any(prompt in last_line for prompt in ['smb:', 'ftp>']):
                    if len(out_line) > 1 and any(prompt in out_line[-2] for prompt in SMB_PROMPTS):
                        shell.execute_cmd('exit')
                        time.sleep(0.5)
                        result = result.rsplit('Action:', 1)[0] + f'Action:{command}\nObservation: '
                        new_output = shell.execute_cmd(command)
                        result += new_output + '\n'
        except Exception as e:
            print(e)
            result = "Before sending a remote command you need to set-up an SSH connection."

        return result



