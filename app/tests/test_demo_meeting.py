"""scripts/demo-meeting.py writes notes the way the engine does: its copied labels must match app/lib/i18n.py."""
import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Sandbox  # noqa: E402
import mainland_usage  # noqa: E402

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
        for family in ("zh", "zh-hans", "en"):
            for key, text in demo.STR[family].items():
                with self.subTest(family=family, key=key):
                    self.assertEqual(text, i18n.STRINGS[family][key])
            self.assertEqual(demo.VERIFY[family], i18n.VERIFY_LABEL[family])

    def test_every_item_kind_has_a_label(self):
        demo = load_demo()
        for meeting, family in ((demo.ZH, "zh"), (demo.ZH_HANS, "zh-hans"), (demo.EN, "en")):
            for _, kind, payload in meeting["events"]:
                if kind == "items":
                    for key, _ in payload:
                        self.assertIn(key, demo.STR[family])

    def test_the_simplified_meeting_is_the_traditional_one(self):
        # Same events, kinds and pacing; the words are i18n.to_hans of the Traditional ones, edited for mainland usage.
        demo = load_demo()
        self.assertEqual([(s, k) for s, k, _ in demo.ZH["events"]], [(s, k) for s, k, _ in demo.ZH_HANS["events"]])
        for (_, kind, zh), (_, _, hans) in zip(demo.ZH["events"], demo.ZH_HANS["events"]):
            if kind == "items":
                self.assertEqual([k for k, _ in zh], [k for k, _ in hans])
        # The data is edited by hand: no Traditional character may be left (OpenCC t2s leaves the text as it is), and
        # no Taiwan usage written in Simplified script (the list the app's texts are held to, plus this meeting's words).
        import opencc
        t2s = opencc.OpenCC("t2s")
        words = mainland_usage.TW_USAGE + ("音档", "完成档", "范式", "寄信", "量测", "来宾", "收音")
        for text in [demo.ZH_HANS["title"], demo.ZH_HANS["me"], demo.ZH_HANS["others"]] + [str(p) for _, _, p in demo.ZH_HANS["events"]]:
            self.assertEqual(t2s.convert(text), text)
            for tw in words:
                self.assertNotIn(tw, text)


if __name__ == "__main__":
    unittest.main()
