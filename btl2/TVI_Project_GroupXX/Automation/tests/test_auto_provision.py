"""Offline tests: ConnectHandler is replaced by a fake, no device or network needed.
    python3 -m unittest -v Automation.tests.test_auto_provision      (from the project root)
"""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import auto_provision as ap  # noqa: E402


class FakeConn:
    log = []

    def __init__(self, **kw):
        self.kw = kw
        FakeConn.log.append(("connect", kw["host"]))
        if kw["host"].endswith(".99.12"):
            raise ap.NetmikoTimeoutException("timed out")
        if kw["host"].endswith(".99.13"):
            raise ap.NetmikoAuthenticationException("bad creds")

    def enable(self):
        pass

    def send_command(self, c):
        import re as _re
        vl = "".join(f"{i}   V{i}   active\n" for i in (10, 20, 21, 30, 40, 50, 60, 61, 62, 99, 100, 110, 998, 999))
        return f"output of {c}\n{vl}"

    def send_config_set(self, cmds):
        FakeConn.log.append(("config", list(cmds)))
        return "ok"

    def save_config(self):
        FakeConn.log.append(("save",))

    def disconnect(self):
        pass


def write_inv(tmp):
    inv = tmp / "inv.yaml"
    inv.write_text("""defaults: {port: 22, device_type: cisco_ios, timeout: 5}
devices:
  - {hostname: SW-A, ip: 10.1.99.11, role: access}
  - {hostname: SW-TIMEOUT, ip: 10.1.99.12, role: access}
  - {hostname: SW-AUTH, ip: 10.1.99.13, role: core}
  - {hostname: RTR-A, ip: 10.1.255.11, role: edge}
""")
    return inv


class Tests(unittest.TestCase):
    def setUp(self):
        FakeConn.log = []
        self.tmp = Path(tempfile.mkdtemp())
        self.inv = write_inv(self.tmp)
        self.args = ["--inventory", str(self.inv), "--payload", str(HERE / "payload.yaml"),
                     "--backup-dir", str(self.tmp / "b"), "--log-dir", str(self.tmp / "l")]
        self.creds = ("u", "SuperSecret!", "SuperSecret!")

    def test_errors_do_not_stop_the_run_and_are_logged(self):
        res = ap.run_automation(self.args, connect=FakeConn, creds=self.creds)
        self.assertEqual(res, {"SW-A": "ok", "SW-TIMEOUT": "failed", "SW-AUTH": "failed", "RTR-A": "ok"})
        log = (self.tmp / "l" / "automation.log").read_text()
        self.assertIn("Connection timeout on SW-TIMEOUT", log)
        self.assertIn("Authentication failed on SW-AUTH", log)
        self.assertIn("Successfully configured SW-A", log)

    def test_password_never_reaches_log_or_backups(self):
        ap.run_automation(self.args, connect=FakeConn, creds=self.creds)
        blob = (self.tmp / "l" / "automation.log").read_text()
        for f in (self.tmp / "b").glob("*"):
            blob += f.read_text()
        self.assertNotIn("SuperSecret!", blob)

    def test_two_backups_per_device_with_required_name(self):
        ap.run_automation(self.args + ["--devices", "SW-A"], connect=FakeConn, creds=self.creds)
        files = sorted((self.tmp / "b").glob("SW-A_*_backup.cfg"))
        self.assertEqual(len(files), 2)
        stages = [f.read_text().splitlines()[1] for f in files]
        self.assertEqual(sorted(stages), ["! stage: after", "! stage: before"])
        self.assertIn("show ip interface brief", files[0].read_text())

    def test_vlans_only_on_switches(self):
        ap.run_automation(self.args + ["--devices", "SW-A,RTR-A", "--vlan", "110:LAB_DEMO"],
                          connect=FakeConn, creds=self.creds)
        cfgs = [e[1] for e in FakeConn.log if e[0] == "config"]
        self.assertEqual(len(cfgs), 2)
        sw, rtr = cfgs
        self.assertIn("vlan 110", sw)
        self.assertIn(" name LAB_DEMO", sw)
        self.assertIn("vlan 10", sw)
        self.assertNotIn("vlan 10", rtr)
        self.assertTrue(any(c.startswith("ntp server") for c in rtr))

    def test_dry_run_changes_nothing(self):
        ap.run_automation(self.args + ["--dry-run"], connect=FakeConn)
        self.assertEqual(FakeConn.log, [])
        self.assertEqual(list((self.tmp / "b").glob("*")), [])

    def test_backup_only_does_not_configure(self):
        ap.run_automation(self.args + ["--backup-only", "--devices", "SW-A"], connect=FakeConn, creds=self.creds)
        self.assertFalse(any(e[0] == "config" for e in FakeConn.log))
        self.assertEqual(len(list((self.tmp / "b").glob("*"))), 1)

    def test_real_inventory_is_valid(self):
        devs = ap.load_inventory(HERE / "inventory.yaml")
        self.assertGreaterEqual(len(devs), 12)
        for d in devs:
            self.assertEqual(d["device_type"], "cisco_ios")
            self.assertTrue(d["ip"])
        text = (HERE / "inventory.yaml").read_text().lower()
        self.assertNotIn("password", text)


if __name__ == "__main__":
    unittest.main()
