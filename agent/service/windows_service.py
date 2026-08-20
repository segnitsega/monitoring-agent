"""Windows Service wrapper (AGENT_SPEC.md §7).

Runs the same :class:`~agent.main.AgentRunner` under the Windows Service Control
Manager via pywin32. Install/remove/start/stop are handled by
``win32serviceutil.HandleCommandLine``:

    monitoring-agent-service.exe install
    monitoring-agent-service.exe start

pywin32 is Windows-only, so its imports are guarded: the module still imports on
Linux/macOS (for unit testing and packaging tooling) but ``main`` refuses to run
the service there. The Windows SCM stop signal and the runner's internal stop
event are bridged so ``SvcStop`` shuts the loop down cleanly.
"""

from __future__ import annotations

import os
import sys
import threading

from agent.config import load_config
from agent.logging_setup import configure_logging, get_logger
from agent.main import AgentRunner

_log = get_logger()

DEFAULT_CONFIG_PATH = r"C:\ProgramData\MonitoringAgent\config.json"
_CONFIG_ENV = "MONITORING_AGENT_CONFIG"

try:  # pragma: no cover - only importable on Windows
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    _HAS_PYWIN32 = True
except ImportError:
    _HAS_PYWIN32 = False


def _resolve_config_path() -> str:
    return os.environ.get(_CONFIG_ENV, DEFAULT_CONFIG_PATH)


if _HAS_PYWIN32:  # pragma: no cover - requires Windows + pywin32

    class MonitoringAgentService(win32serviceutil.ServiceFramework):
        _svc_name_ = "MonitoringAgent"
        _svc_display_name_ = "Server Monitoring Agent"
        _svc_description_ = (
            "Collects server health metrics and backup evidence and reports "
            "them to the monitoring portal."
        )

        def __init__(self, args) -> None:
            super().__init__(args)
            self._scm_stop = win32event.CreateEvent(None, 0, 0, None)
            self._runner: AgentRunner | None = None

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            if self._runner is not None:
                self._runner.stop()
            win32event.SetEvent(self._scm_stop)

        def SvcDoRun(self) -> None:
            servicemanager.LogInfoMsg(f"{self._svc_name_} starting")
            try:
                self._run()
            except Exception as exc:  # noqa: BLE001
                servicemanager.LogErrorMsg(f"{self._svc_name_} failed: {exc}")
                raise
            servicemanager.LogInfoMsg(f"{self._svc_name_} stopped")

        def _run(self) -> None:
            config = load_config(_resolve_config_path())
            configure_logging(config.log_file, config.log_level)
            stop = threading.Event()
            self._runner = AgentRunner(config, stop_event=stop)
            worker = threading.Thread(target=self._runner.run_forever, daemon=True)
            worker.start()
            # Block until the SCM asks us to stop.
            win32event.WaitForSingleObject(self._scm_stop, win32event.INFINITE)
            stop.set()
            worker.join(timeout=30)
            self._runner.close()

else:
    MonitoringAgentService = None  # type: ignore[assignment]


def main(argv: list[str] | None = None) -> int:
    if not _HAS_PYWIN32:
        print(
            "pywin32 is required for the Windows service wrapper "
            "(install on Windows: pip install pywin32).",
            file=sys.stderr,
        )
        return 1

    argv = list(sys.argv if argv is None else argv)

    # When running as a frozen service with no control verb, hand off to the SCM.
    if getattr(sys, "frozen", False) and len(argv) == 1:  # pragma: no cover - Windows only
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(MonitoringAgentService)
        servicemanager.StartServiceCtrlDispatcher()
        return 0

    win32serviceutil.HandleCommandLine(MonitoringAgentService, argv=argv)  # pragma: no cover
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
