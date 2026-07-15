from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DispatcherServiceTests(unittest.TestCase):
    def test_dispatcher_polls_without_claiming_and_starts_collector(self):
        script = (ROOT / "bin" / "dispatch-queued").read_text()
        self.assertIn("site-requests", script)
        self.assertNotIn("site-claim", script)
        self.assertIn("systemctl --user start --no-block x-radar.service", script)

    def test_installer_enables_minute_dispatch_timer(self):
        installer = (ROOT / "bin" / "install-user-services").read_text()
        timer = (ROOT / "deploy" / "systemd" / "x-radar-dispatch.timer").read_text()
        self.assertIn("x-radar-dispatch.timer", installer)
        self.assertIn("OnUnitActiveSec=1m", timer)

    def test_system_service_can_dispatch_inline_without_user_systemd(self):
        dispatcher = (ROOT / "bin/dispatch-queued").read_text()
        self.assertIn("XRADAR_DISPATCH_INLINE", dispatcher)
        self.assertIn('exec "$ROOT/bin/collect-once"', dispatcher)

    def test_collector_defaults_to_networked_workspace_sandbox(self):
        collector = (ROOT / "bin" / "collect-once").read_text()
        self.assertIn('XRADAR_COLLECTOR_ORCHESTRATION:-pipeline', collector)
        self.assertIn('"$ROOT/bin/collect-pipeline"', collector)
        self.assertIn('ORCHESTRATION" != "agent"', collector)
        self.assertIn("--sandbox workspace-write", collector)
        self.assertIn("approval_policy=never", collector)
        self.assertIn("sandbox_workspace_write.network_access=true", collector)
        self.assertIn("XRADAR_CODEX_UNSANDBOXED", collector)
        self.assertNotIn("XRADAR_LUNA_MODEL", collector)
        self.assertIn("-m gpt-5.6-luna", collector)

    def test_single_machine_installer_keeps_dashboard_local_and_persistent(self):
        installer = (ROOT / "bin" / "install-single-machine").read_text()
        service = (ROOT / "deploy" / "systemd" / "x-radar-dashboard.service").read_text()
        self.assertIn("XRADAR_MODE=single", installer)
        self.assertIn("http://127.0.0.1:8787", installer)
        self.assertIn("--ip 127.0.0.1", service)
        self.assertIn("--persist-to %h/x-radar/var/single-machine", service)
        self.assertIn("--env-file %h/x-radar/.env", service)


if __name__ == "__main__":
    unittest.main()
