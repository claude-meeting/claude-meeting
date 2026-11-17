"""Unit tests for app/lib/config.py: loading, defaults, path helpers and the test seams."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import FAKES_BIN, Sandbox  # noqa: E402


class ConfigLoading(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.config = self.sb.import_module("config")

    def tearDown(self):
        self.sb.cleanup()

    def test_defaults_come_from_the_example_and_the_user_file_wins(self):
        cfg = self.config.load()
        self.assertEqual(cfg["user"]["name"], "Alex")                     # from the sandbox config
        self.assertIn("tick_secs", cfg["copilot"])                         # from config.example.toml
        self.assertEqual(cfg["autopilot"]["start_after_secs"], 4)
        self.assertIsNone(self.config.LOAD_ERROR)

    def test_missing_user_file_means_defaults(self):
        self.sb.config_path.unlink()
        cfg = self.config.load()
        self.assertEqual(cfg["user"]["name"], "")
        self.assertIsNone(self.config.LOAD_ERROR)

    def test_broken_user_file_is_reported_not_fatal(self):
        self.sb.config_path.write_text('[user\nname = "x"\n')
        cfg = self.config.load()
        self.assertIn("name", cfg["user"])
        self.assertIn("config.toml", self.config.LOAD_ERROR)
        self.sb.config_path.write_text('[user]\nname = "ok"\n')
        self.config.load()
        self.assertIsNone(self.config.LOAD_ERROR)                          # cleared once the file is fine again

    def test_non_utf8_user_file_is_reported_not_fatal(self):
        self.sb.config_path.write_bytes('[user]\nname = "毛毛蟲"\n'.encode("big5"))
        self.config.load()
        self.assertIn("decode", self.config.LOAD_ERROR)

    def test_expand_and_paths(self):
        c = self.config
        self.assertIsNone(c.expand(""))
        self.assertEqual(c.expand("~/x"), self.sb.home / "x")
        self.assertEqual(c.meetings_dir({}), self.sb.home / "meetings")
        self.assertEqual(c.meetings_dir({"paths": {"meetings_dir": "/tmp/m"}}), Path("/tmp/m"))
        self.assertEqual(c.minutes_app({}), Path("/Applications/Minutes.app"))
        self.assertEqual(c.minutes_cli({"paths": {"minutes_app": "~/M.app"}}),
                         str(self.sb.home / "M.app" / "Contents" / "MacOS" / "minutes"))

    def test_known_terms_file_precedence(self):
        c = self.config
        os.environ.pop("MEETING_COPILOT_KNOWN_TERMS", None)
        self.assertEqual(c.known_terms_file({}), self.sb.home / ".config" / "meeting-copilot" / "known_terms.txt")
        self.assertEqual(c.known_terms_file({"explain": {"known_terms_file": "~/k.txt"}}), self.sb.home / "k.txt")
        os.environ["MEETING_COPILOT_KNOWN_TERMS"] = "/tmp/env.txt"
        self.assertEqual(c.known_terms_file({"explain": {"known_terms_file": "~/k.txt"}}), Path("/tmp/env.txt"))

    def test_state_dir_and_tool_seams(self):
        c = self.config
        self.assertEqual(c.state_dir(), self.sb.state)
        os.environ.pop("MEETING_COPILOT_STATE")
        self.assertEqual(c.state_dir(), c.APP_DIR / "state")
        self.assertEqual(c.tool("minutes", "/x/minutes"), str(FAKES_BIN / "minutes"))      # fake exists
        self.assertEqual(c.tool("no-such-tool", "/x/tool"), "/x/tool")                     # falls back
        os.environ.pop("MEETING_COPILOT_BIN")
        self.assertEqual(c.tool("minutes", "/x/minutes"), "/x/minutes")

    def test_claude_cli_lookup_order(self):
        c = self.config
        configured = self.sb.root / "my-claude"
        configured.write_text("#!/bin/sh\n")
        self.assertEqual(c.claude_cli({"paths": {"claude_cli": str(configured)}}), str(configured))
        self.assertEqual(c.claude_cli({"paths": {"claude_cli": "/nonexistent"}}), str(FAKES_BIN / "claude"))  # PATH
        os.environ["PATH"] = "/usr/bin:/bin"
        local = self.sb.home / ".local" / "bin" / "claude"
        self.assertEqual(c.claude_cli({}), "claude")                       # nothing anywhere: bare name
        local.parent.mkdir(parents=True)
        local.write_text("#!/bin/sh\n")
        self.assertEqual(c.claude_cli({}), str(local))

    def test_language_family(self):
        c = self.config
        self.assertEqual(c.language_family({"user": {"language": "zh-TW"}}), "zh")
        self.assertEqual(c.language_family({"user": {"language": "en-US"}}), "en")
        self.assertEqual(c.language_family({"user": {"language": "zh-CN"}}), "zh-hans")
        import i18n
        saved = i18n.macos_language
        try:
            i18n.macos_language = lambda: "zh-Hant-TW"
            self.assertEqual(c.language_family({"user": {"language": ""}}), "zh")
            i18n.macos_language = lambda: ""
            self.assertEqual(c.language_family({}), "en")
        finally:
            i18n.macos_language = saved

    def test_asr_model_resolution(self):
        c = self.config
        zh = {"user": {"language": "zh-TW"}, "asr": {"primary_model": "auto", "fallback_model": "auto"}}
        self.assertEqual(c.asr_model(zh, "primary_model"), c.AUTO_PRIMARY["zh"])
        self.assertEqual(c.asr_model(zh, "fallback_model"), c.AUTO_FALLBACK["zh"])
        hans = {"user": {"language": "zh-CN"}, "asr": {"primary_model": "auto", "fallback_model": "auto"}}
        self.assertEqual(c.asr_model(hans, "primary_model"), "mlx-community/Qwen3-ASR-1.7B-8bit")  # writes Simplified
        self.assertEqual(c.asr_model(hans, "fallback_model"), "mlx-community/Qwen3-ASR-0.6B-8bit")
        local = self.sb.root / "model-dir"
        local.mkdir()
        self.assertEqual(c.asr_model({"asr": {"primary_model": str(local)}}, "primary_model"), str(local))
        converted = c.models_dir() / "TEA-ASR-1.1-8bit"
        self.assertEqual(c.models_dir(), self.sb.root / "models")             # the sandbox's, not app/models
        converted.mkdir(parents=True)
        self.assertEqual(c.asr_model(zh, "primary_model"), str(converted))
        self.assertEqual(c.asr_model_missing(zh), "")
        os.environ.pop("MEETING_COPILOT_MODELS")
        self.assertEqual(c.models_dir(), c.APP_DIR / "models")
        self.assertEqual(c.asr_model({"asr": {"primary_model": "org/some-model"}}, "primary_model"), "org/some-model")

    def test_asr_model_missing_after_a_language_change(self):
        c = self.config
        zh = {"user": {"language": "zh-TW"}, "asr": {"primary_model": "auto", "fallback_model": "auto"}}
        self.assertEqual(c.asr_model_missing(zh), "")                      # the sandbox caches the defaults
        self.sb.uncache_models()
        self.assertEqual(c.asr_model_missing(zh), c.AUTO_PRIMARY["zh"])
        self.assertEqual(c.asr_model_missing(zh, "fallback_model"), c.AUTO_FALLBACK["zh"])
        self.sb.cache_models(c.AUTO_FALLBACK["zh"])
        self.assertEqual(c.asr_model_missing(zh, "fallback_model"), "")
        local = self.sb.root / "model-dir"
        local.mkdir()
        self.assertEqual(c.asr_model_missing({"asr": {"primary_model": str(local)}}), "")

    def test_asr_model_missing_for_a_folder_that_is_gone(self):
        # A configured model folder that was deleted: reported as missing, never an exception (this check runs
        # when the service starts; an invalid Hugging Face id used to stop it).
        c = self.config
        for path in ("~/gone/TEA-ASR-1.1-8bit", "/nonexistent/model", "./relative/model", "not a repo id"):
            self.assertEqual(c.asr_model_missing({"asr": {"primary_model": path}}), path)
        self.assertEqual(c.asr_model_missing({"asr": {"primary_model": "~"}}), "")         # exists


if __name__ == "__main__":
    unittest.main()
