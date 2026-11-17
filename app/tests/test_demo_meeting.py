"""scripts/demo-meeting.py writes notes the way the engine does: its copied labels must match app/lib/i18n.py."""
import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Sandbox  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def load_demo():
    spec = importlib.util.spec_from_file_location("demo_meeting", ROOT / "scripts" / "demo-meeting.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DemoMatchesEngine(unittest.TestCase):
    def test_labels_are_the_engines(self):
        sandbox = Sandbox()
        self.addCleanup(sandbox.cleanup)
        i18n = sandbox.import_module("i18n")
        demo = load_demo()
        for family in ("zh", "en"):
            for key, text in demo.STR[family].items():
                with self.subTest(family=family, key=key):
                    self.assertEqual(text, i18n.STRINGS[family][key])
            self.assertEqual(demo.VERIFY[family], i18n.VERIFY_LABEL[family])

    def test_every_item_kind_has_a_label(self):
        demo = load_demo()
        for meeting, family in ((demo.ZH, "zh"), (demo.EN, "en")):
            for _, kind, payload in meeting["events"]:
                if kind == "items":
                    for key, _ in payload:
                        self.assertIn(key, demo.STR[family])


if __name__ == "__main__":
    unittest.main()
