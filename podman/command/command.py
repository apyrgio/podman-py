import contextlib
import platform
import subprocess
import time
from pathlib import Path
from typing import Optional, Union

from . import cli_runner, machine_manager
from .. import client, errors


class PodmanCommand:
    """Main class for executing Podman commands.

    Attributes:
        runner (cli_runner.Runner): The runner instance to execute commands.
        machine (machine_manager.MachineManager): Manager for machine operations.
    """

    GlobalOptions = cli_runner.GlobalOptions

    def __init__(
        self,
        path: Path = None,
        privileged: bool = False,
        options: cli_runner.GlobalOptions = None,
        env: dict = None,
    ):
        """Initialize the PodmanCommand.

        Args:
            path (Path, optional): Path to the Podman executable. Defaults to None.
            privileged (bool, optional): Whether to run commands with elevated privileges. Defaults to False.
            options (cli_runner.GlobalOptions, optional): Global options for Podman commands. Defaults to a new instance of GlobalOptions.
            env (dict, optional): Environment variables for the subprocess. Defaults to None.
        """
        if options is None:
            options = cli_runner.GlobalOptions()
        self.runner = cli_runner.Runner(
            path=path,
            privileged=privileged,
            options=options,
            env=env,
        )
        self.machine = machine_manager.MachineManager(self.runner)

    def run(
        self,
        cmd: list[str],
        *,
        check: bool = True,
        capture_output=True,
        wait=True,
        **skwargs,
    ) -> Union[str, subprocess.Popen]:
        """Run the specified Podman command.

        Args:
            cmd (list[str]): The command to run, as a list of strings.
            check (bool, optional): Whether to check for errors. Defaults to True.
            capture_output (bool, optional): Whether to capture output. Defaults to True.
            wait (bool, optional): Whether to wait for the command to complete. Defaults to True.
            **skwargs: Additional keyword arguments for subprocess.

        Returns:
            Optional[str]: The output of the command if captured, otherwise the
                subprocess.Popen instance.

        Raises:
            errors.CommandError: If the command fails.
        """
        return self.runner.run(
            cmd=cmd, check=check, capture_output=capture_output, wait=wait, **skwargs
        )

    @property
    def options(self) -> cli_runner.GlobalOptions:
        """Returns the global options for this Podman command instance."""
        return self.runner.options

    def start_service(
        self,
        uri: Optional[str] = None,
        time: Optional[int] = None,
        cors: Optional[str] = None,
        **skwargs,
    ) -> subprocess.Popen:
        """Start the Podman system service.

        This method starts a REST API using Podman's `system service` command.
        This method is available only on Linux systems.

        Args:
            uri (str, optional): The URI for the service. Uses the default URI if not specified.
            time (str, optional): How long should the service be up. Default is 5 seconds.
            cors (str, optional): CORS settings for the service.
            **skwargs: Additional keyword arguments for subprocess.

        Returns:
            subprocess.Popen: The process handle of the `podman system service`
            command.
        """
        if platform.system() != "Linux":
            raise errors.PodmanError(
                "The `podman system service` command is available only on Linux systems"
            )

        cmd = self.runner.construct("system", "service", uri, time=time, cors=cors)
        return self.runner.run_raw(cmd, wait=False, **skwargs)

    def stop_service(
        self,
        proc: subprocess.Popen,
        timeout: Optional[int] = None,
    ) -> int:
        """Stop the Podman system service.

        This method stops the Podman REST API service.

        Args:
            proc (subprocess.Popen): The process handle for Podman's system service.
            timeout (int, optional): How long to wait until the service stops.

        Returns:
            int: The exit code of the service process.
        """
        proc.terminate()
        try:
            ret = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            ret = proc.wait()
        return ret

    def wait_for_service(
        self,
        uri: str,
        proc: subprocess.Popen,
        timeout: int = None,
        check_interval: float = 0.1,
    ):
        """Wait for the Podman system service to be operational.

        This method checks two things; if the system service is still running,
        and if we can ping it successfully.

        Args:
            uri (str): The URI for the service.
            proc (subprocess.Popen): The process handle for Podman's system service.
            timeout (int, optional): How long to wait until the service is operational
            check_interval (float): The interval between health checks

        Returns:
            int: The exit code of the service process.
        """
        start = time.monotonic()
        with client.PodmanClient(base_url=uri) as c:
            while True:
                if timeout and time.monotonic() - start > timeout:
                    raise errors.ServiceTimeout(timeout)

                ret = proc.poll()
                if ret is not None:
                    raise errors.ServiceTerminated(ret)

                try:
                    if c.ping():
                        break
                except errors.APIError:
                    pass
                time.sleep(check_interval)

    @contextlib.contextmanager
    def service(
        self,
        uri: str,
        cors: str = None,
        ping_timeout: int = None,
        stop_timeout: int = None,
        **skwargs,
    ) -> subprocess.Popen:
        """Manage the Podman system service.

        This method starts a REST API using Podman's `system service` command
        and yields the process back to the user. Once the user does not need
        the REST API any more, it stops the Podman service.

        Args:
            uri (str): The URI for the service.
            cors (str, optional): CORS settings for the service.
            ping_timeout (int, optional): How long to wait until the service is up.
            stop_timeout (int, optional): How long to wait until the service stops.
            **skwargs: Additional keyword arguments for subprocess.

        Returns:
            subprocess.Popen: The process handle of the `podman system service` command.
        """
        proc = self.start_service(uri=uri, time=0, cors=cors, **skwargs)
        try:
            self.wait_for_service(uri, proc, timeout=ping_timeout)
        except (errors.ServiceTimeout, errors.ServiceTerminated):
            self.stop_service(proc, timeout=stop_timeout)
            raise

        yield proc
        self.stop_service(proc, timeout=stop_timeout)
