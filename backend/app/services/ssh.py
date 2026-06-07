import asyncssh
import asyncio
import os
from app.core.config import settings

class SSHRunner:
    def __init__(self):
        self.username = settings.ssh_username

    async def execute(self, ip: str, port: int, command: str, ticket_id: int, timeout: int = 30) -> str:
        case_num = ticket_id % 10
        key_path = f"/keys/case{case_num}_key.pem"
        
        # Fallback to configured key path if the specific case key doesn't exist in container
        if not os.path.exists(key_path):
            if os.path.exists(settings.ssh_private_key_path.replace("./", "/app/")):
                key_path = settings.ssh_private_key_path.replace("./", "/app/")
            elif os.path.exists(settings.ssh_private_key_path.replace("./", "/")):
                key_path = settings.ssh_private_key_path.replace("./", "/")
            else:
                key_path = "/keys/dummy.pem"

        try:
            async with asyncssh.connect(
                ip, 
                port=port, 
                username=self.username, 
                client_keys=[key_path],
                known_hosts=None # Avoid known hosts prompt for dynamic IPs
            ) as conn:
                result = await asyncio.wait_for(conn.run(command), timeout=timeout)
                return f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        except asyncio.TimeoutError:
            return "Command execution timed out."
        except Exception as e:
            return f"SSH Error: {str(e)}"

ssh_runner = SSHRunner()