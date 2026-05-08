
import pexpect
from pydantic import BaseModel


class RunCode(BaseModel):

    timeout: int = 60
    commands: list

    def execute_cmd(self):
        output = ""
        process = None

        for i, command in enumerate(self.commands):
            # output += f'Command: {command.strip()}\n'
            if process is None:
                result = self.run_cmd_with_timeout(command)
            else:
                process.sendline(command.strip())

                try:
                    process.expect(pexpect.EOF, timeout=self.timeout)
                    output += f"Response : {process.before.decode()}\n"

                except pexpect.exceptions.TIMEOUT:
                    if i == len(self.commands) - 1:
                        output += f"Response : {process.before.decode()}\n"
                result = process

            if isinstance(result, str):
                output += result
                process = None
            elif isinstance(result, pexpect.spawn):
                process = result

        return output.strip()


    def run_cmd_with_timeout(self, command: str):
        cmd = command.strip()
        output = ""
        try:
            process = pexpect.spawn(cmd, timeout=self.timeout)
            try:
                process.expect(pexpect.EOF, timeout=self.timeout)
                output += f"Response: {process.before.decode()}\n"
                return output
            except pexpect.exceptions.TIMEOUT:
                return process

        except Exception as e:
            return f"Exception occurred: {str(e)}"
