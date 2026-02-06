"""WebSocket consumers for Wintermute terminal access."""

import asyncio
import json
import logging
import shlex
import subprocess

from channels.generic.websocket import AsyncWebsocketConsumer

from .models import Agent, AgentSession, VMTarget

logger = logging.getLogger(__name__)


class TerminalConsumer(AsyncWebsocketConsumer):
    """WebSocket consumer that proxies to a tmux session on a remote VM."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_id = None
        self.ssh_process = None
        self.reader_task = None

    async def connect(self):
        """Handle WebSocket connection."""
        self.session_id = self.scope['url_route']['kwargs']['session_id']

        # Verify user is authenticated
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        # Get session and verify it exists
        try:
            session = await asyncio.to_thread(AgentSession.objects.select_related().get, id=self.session_id)
        except AgentSession.DoesNotExist:
            await self.close(code=4004)
            return

        # Get agent and VM target
        try:
            agent = await asyncio.to_thread(Agent.objects.get, id=session.agent_id)
            if not agent.vm_target_id:
                await self.close(code=4002)
                return
            vm = await asyncio.to_thread(VMTarget.objects.get, id=agent.vm_target_id)
        except (Agent.DoesNotExist, VMTarget.DoesNotExist):
            await self.close(code=4004)
            return

        # Accept the WebSocket connection
        await self.accept()

        # Start SSH connection to tmux
        await self.start_ssh_connection(vm, agent, session)

    async def start_ssh_connection(self, vm, agent, session):
        """Start SSH connection to attach to tmux session."""
        tmux_session_name = f"wm_{session.id}"

        # Build SSH command to attach to tmux
        ssh_options = []
        if agent.required_ssh_options:
            ssh_options = shlex.split(agent.required_ssh_options)

        # Add BatchMode and other options for non-interactive SSH
        ssh_options.extend(["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"])

        # Request a pty for terminal interaction
        ssh_cmd = [
            "ssh",
            "-t",
            "-t", # Force PTY allocation
            "-p",
            str(vm.port),
            *ssh_options,
            f"{vm.user}@{vm.host}",
            f"tmux attach-session -t {shlex.quote(tmux_session_name)}"
        ]

        logger.info("Starting terminal connection: %s", " ".join(ssh_cmd))

        try:
            # Start SSH process with PTY
            self.ssh_process = await asyncio.create_subprocess_exec(
                *ssh_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            # Start reading from SSH stdout
            self.reader_task = asyncio.create_task(self.read_ssh_output())

        except Exception as e:
            logger.error("Failed to start SSH connection: %s", e)
            await self.send(text_data=json.dumps({"type": "error", "message": f"Failed to connect: {e}"}))
            await self.close()

    async def read_ssh_output(self):
        """Read output from SSH and send to WebSocket."""
        try:
            while True:
                if self.ssh_process is None or self.ssh_process.stdout is None:
                    break

                # Read available data
                data = await self.ssh_process.stdout.read(4096)
                if not data:
                    break

                # Send to WebSocket as binary (terminal data)
                await self.send(bytes_data=data)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Error reading SSH output: %s", e)
        finally:
            # Notify client that connection closed
            try:
                await self.send(text_data=json.dumps({"type": "disconnect", "message": "Terminal session ended"}))
            except Exception:
                pass

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        # Cancel reader task
        if self.reader_task:
            self.reader_task.cancel()
            try:
                await self.reader_task
            except asyncio.CancelledError:
                pass

        # Terminate SSH process (but don't kill tmux session)
        if self.ssh_process:
            try:
                self.ssh_process.terminate()
                await asyncio.wait_for(self.ssh_process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self.ssh_process.kill()
            except Exception:
                pass

    async def receive(self, text_data=None, bytes_data=None):
        """Handle incoming WebSocket data."""
        if self.ssh_process is None or self.ssh_process.stdin is None:
            return

        try:
            if bytes_data:
                # Binary data - send directly to SSH stdin
                self.ssh_process.stdin.write(bytes_data)
                await self.ssh_process.stdin.drain()
            elif text_data:
                # Text data - could be JSON commands or text input
                try:
                    msg = json.loads(text_data)
                    if msg.get("type") == "resize":
                        # Handle terminal resize
                        cols = msg.get("cols", 80)
                        rows = msg.get("rows", 24)
                        await self.resize_terminal(cols, rows)
                    elif msg.get("type") == "input":
                        # Input text
                        data = msg.get("data", "")
                        self.ssh_process.stdin.write(data.encode("utf-8"))
                        await self.ssh_process.stdin.drain()
                except json.JSONDecodeError:
                    # Plain text input
                    self.ssh_process.stdin.write(text_data.encode("utf-8"))
                    await self.ssh_process.stdin.drain()
        except Exception as e:
            logger.error("Error sending data to SSH: %s", e)

    async def resize_terminal(self, cols, rows):
        """Resize the remote terminal."""
        # For tmux, we can use tmux's resize commands
        # This is a best-effort approach
        if self.ssh_process and self.ssh_process.stdin:
            # Send escape sequence to resize (this may not work perfectly)
            # A better approach would be to use SIGWINCH on the SSH process
            pass
