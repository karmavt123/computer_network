"""Mock-based tests for Lab/collect_evidence.py and the change-diff of auto_provision.py (no devices needed).

Run from the project root:  python3 -m unittest -v Automation.tests.test_collect_evidence
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Automation"))
sys.path.insert(0, str(ROOT / "Lab"))
import auto_provision as ap          # noqa: E402
import collect_evidence as ce        # noqa: E402


class FakeConn:
    """Stands in for a Netmiko connection; answers every command with a canned text."""
    log = []

    def __init__(self, **kw):
        self.host = kw["host"]
        if self.host == "10.1.99.3":                    # second core is 'down'
            raise ap.NetmikoTimeoutException("timed out")

    def enable(self):
        pass

    def send_command(self, cmd, **_kw):
        FakeConn.log.append((self.host, cmd))
        if "etherchannel" in cmd:
            return "1      Po1(SU)         LACP      Gi0/0(P)    Gi0/1(P)"
        if "vlan brief" in cmd:
            return "10   EXEC_FINANCE   active\n20   RND_ENGINEERING active\n"     # MANAGEMENT missing -> CHECK
        return f"output of {cmd}"

    def disconnect(self):
        pass


class CollectEvidence(unittest.TestCase):
    def setUp(self):
        FakeConn.log = []
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_is_valid_and_read_only(self):
        plan = ce.load_plan(ROOT / "Lab" / "evidence_plan.yaml")
        ids = [it["id"] for it in plan]
        self.assertEqual(len(ids), len(set(ids)), "duplicate evidence ids")
        self.assertGreaterEqual(len(plan), 40)
        inv = {d["hostname"] for d in ap.load_inventory(ROOT / "Automation" / "inventory.yaml")}
        for it in plan:
            self.assertTrue(it.get("manual") or it["device"] in inv, it["id"])

    def test_non_read_only_command_is_refused(self):
        bad = self.out / "bad.yaml"
        bad.write_text("evidence:\n- {id: x1, device: A, title: t, commands: ['reload']}\n")
        with self.assertRaises(ValueError):
            ce.load_plan(bad)

    def test_files_verdicts_and_unreachable_device(self):
        rc = ce.run(["--only", "s3_*", "--out", str(self.out)], connect=FakeConn, creds=("u", "p", "p"))
        self.assertEqual(rc, 1)                                        # CORE-SW02 timed out
        idx = {r["id"]: r for r in json.loads((self.out / "index.json").read_text())}
        self.assertEqual(idx["s3_3"]["verdict"], "PASS")
        self.assertEqual(idx["s3_1"]["verdict"], "CHECK")              # expected VLAN name missing
        self.assertNotIn("s3_5", idx)                                  # unreachable device: no file, no entry
        text = (self.out / "s3_3.txt").read_text()
        self.assertIn("HCMC-CORE-SW01#show etherchannel summary", text)
        self.assertIn("Po1(SU)", text)

    def test_tag_suffix_for_failover_snapshots(self):
        ce.run(["--only", "s10_1", "--tag", "core1_down", "--out", str(self.out)], connect=FakeConn,
               creds=("u", "p", "p"))
        self.assertTrue((self.out / "s10_1_core1_down.txt").exists())
        self.assertTrue((self.out / "index_core1_down.json").exists())


class ChangeDiff(unittest.TestCase):
    def test_diff_counts_only_real_changes(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            a, b = t / "SW_20260101_000000_backup.cfg", t / "SW_20260101_000005_backup.cfg"
            a.write_text("! host: SW\n! stage: before\n! captured: x\nvlan 10\n name EXEC\n")
            b.write_text("! host: SW\n! stage: after\n! captured: y\nvlan 10\n name EXEC\nvlan 110\n name LAB_DEMO\n")
            path, changed = ap.write_diff(a, b, t, "SW")
            self.assertEqual(changed, 2)
            self.assertIn("+vlan 110", path.read_text())

    def test_role_specific_skip(self):
        payload = ap.load_yaml(ROOT / "Automation" / "payload.yaml")
        srv = ap.commands_for({"hostname": "X", "role": "dcsrv"}, payload)
        sw = ap.commands_for({"hostname": "Y", "role": "access"}, payload)
        self.assertFalse(any(c.startswith("ntp server") for c in srv))
        self.assertTrue(any(c.startswith("ntp server") for c in sw))


if __name__ == "__main__":
    unittest.main(verbosity=2)
