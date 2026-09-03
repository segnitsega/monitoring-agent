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
from dataclasses import replace

from agent.config import AgentConfig, ConfigError, load_config
from agent.logging_setup import configure_logging, get_logger
from agent.main import AgentRunner
from agent.transport.register import RegistrationError, ensure_registered

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
            self.ReportServiceStatus(win32service.SERVICE_START_PENDING)
            try:
                self._run()
            except Exception as exc:  # noqa: BLE001
                servicemanager.LogErrorMsg(f"{self._svc_name_} failed: {exc}")
                raise
            finally:
                self.ReportServiceStatus(win32service.SERVICE_STOPPED)
            servicemanager.LogInfoMsg(f"{self._svc_name_} stopped")

        def _run(self) -> None:
            program_data = r"C:\ProgramData\MonitoringAgent"
            try:
                os.makedirs(program_data, exist_ok=True)
                os.chdir(program_data)
            except OSError:
                pass

            config_path = _resolve_config_path()
            default_log_file = os.path.join(program_data, "agent.log")

            # Setup initial logging to safe file so all startup logs are captured
            log_file = default_log_file
            try:
                if os.path.exists(config_path):
                    cfg_preview = load_config(config_path)
                    log_file = cfg_preview.log_file or default_log_file
                    configure_logging(log_file, cfg_preview.log_level)
                else:
                    configure_logging(log_file, "INFO")
            except Exception:
                configure_logging(log_file, "INFO")

            _log.info("MonitoringAgent Windows Service initialization starting")

            # Report SERVICE_RUNNING so Windows SCM knows the service started cleanly
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)

            stop_event = threading.Event()

            def _is_stop_requested(timeout_sec: float) -> bool:
                res = win32event.WaitForSingleObject(self._scm_stop, int(timeout_sec * 1000))
                return res == win32event.WAIT_OBJECT_0

            config: AgentConfig | None = None
            while not _is_stop_requested(0):
                try:
                    config = load_config(config_path)

                    # Ensure relative queuePath resolves relative to ProgramData
                    if config.queue_path and not os.path.isabs(config.queue_path):
                        abs_queue = os.path.join(program_data, config.queue_path)
                        config = replace(config, queue_path=abs_queue)

                    # Re-configure logging with validated config settings
                    active_log_file = config.log_file or default_log_file
                    configure_logging(active_log_file, config.log_level)

                    if config.needs_registration:
                        try:
                            config = ensure_registered(config, config_path)
                            _log.info("Registration successful for serverId=%s", config.server_id)
                        except (RegistrationError, ConfigError) as reg_exc:
                            _log.warning(
                                "Agent registration required but failed: %s. Retrying in 10s...",
                                reg_exc,
                            )
                            servicemanager.LogWarningMsg(
                                f"MonitoringAgent registration failed: {reg_exc}. Retrying in 10s..."
                            )
                            if _is_stop_requested(10):
                                break
                            continue
                        except Exception as exc:
                            _log.exception("Unexpected error during registration: %s. Retrying in 10s...", exc)
                            if _is_stop_requested(10):
                                break
                            continue

                    # Successfully loaded and registered config
                    break

                except ConfigError as cfg_exc:
                    _log.error("Invalid or missing config at %s: %s. Retrying in 10s...", config_path, cfg_exc)
                    servicemanager.LogErrorMsg(f"MonitoringAgent config error: {cfg_exc}")
                    if _is_stop_requested(10):
                        break
                except Exception as exc:
                    _log.exception("Unexpected startup error: %s. Retrying in 10s...", exc)
                    if _is_stop_requested(10):
                        break

            if config is None or _is_stop_requested(0):
                _log.info("MonitoringAgent service received stop request before starting monitoring loop")
                return

            _log.info(
                "Starting AgentRunner monitoring loop: serverId=%s endpoint=%s interval=%ds",
                config.server_id,
                config.health_endpoint,
                config.interval_seconds,
            )
            self._runner = AgentRunner(config, stop_event=stop_event)
            worker = threading.Thread(target=self._runner.run_forever, daemon=True)
            worker.start()

            # Wait until SCM sends stop signal
            win32event.WaitForSingleObject(self._scm_stop, win32event.INFINITE)
            _log.info("MonitoringAgent SCM stop signal received; shutting down runner...")
            stop_event.set()
            if self._runner is not None:
                self._runner.stop()
            worker.join(timeout=30)
            if self._runner is not None:
                self._runner.close()
            _log.info("MonitoringAgent service shutdown complete")

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
