"""The door to the model (lib/llm.py): the user's Claude subscription through Claude Code, or any service that speaks
the OpenAI chat format. The service here is a stand-in on this Mac; nothing leaves it."""
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import FAKES_BIN, FakeOpenAI, Sandbox  # noqa: E402

IMAGE = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}}


class Base(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.llm = self.sb.import_module("llm")
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.close()
        self.sb.cleanup()

    def service(self, **kw):
        self.servers.append(FakeOpenAI(**kw))
        return self.servers[-1]


class OpenAIFormat(Base):
    def test_one_reply(self):
        fake = self.service(reply="1 Q\n")
        model = self.llm.OpenAICompatible(fake.url + "/", key="sk-test", model="some-model")
        self.assertEqual(model.complete("新的句子", "some-model"), "1 Q\n")
        sent, = fake.requests
        self.assertEqual((sent["path"], sent["auth"]), ("/v1/chat/completions", "Bearer sk-test"))
        self.assertEqual(sent["agent"], "meeting-copilot")     # some services turn Python's own name away
        self.assertEqual(sent["body"]["model"], "some-model")
        self.assertFalse(sent["body"]["stream"])
        system, user = sent["body"]["messages"]
        self.assertEqual((system["role"], user), ("system", {"role": "user", "content": "新的句子"}))
        self.assertIn("meeting assistant", system["content"])
        self.assertIsNone(model.problem())

    def test_a_model_on_this_mac_needs_no_key(self):
        fake = self.service()
        self.llm.OpenAICompatible(fake.url).complete("hi", "local")
        self.assertIsNone(fake.requests[0]["auth"])

    def test_images_go_only_to_a_model_that_reads_them(self):
        fake = self.service()
        self.llm.OpenAICompatible(fake.url).complete("畫面上有什麼", "m", images=[IMAGE])
        self.assertEqual(fake.requests[-1]["body"]["messages"][1]["content"], "畫面上有什麼")
        self.llm.OpenAICompatible(fake.url, vision=True).complete("畫面上有什麼", "m", images=[IMAGE])
        self.assertEqual(fake.requests[-1]["body"]["messages"][1]["content"], [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
            {"type": "text", "text": "畫面上有什麼"}])

    def test_a_streamed_reply_comes_as_it_is_written(self):
        fake = self.service(reply="對象：大家，在提問\n問題\n什麼是 gRPC\n一種遠端呼叫的協定。\n")
        stream = self.llm.OpenAICompatible(fake.url).stream("什麼是 gRPC", "m")
        pieces = list(stream)
        stream.close()
        self.assertEqual(len(pieces), 4)                               # the model's reasoning is not part of the reply
        self.assertEqual("".join(pieces), fake.reply)
        self.assertTrue(fake.requests[0]["body"]["stream"])

    def test_closing_a_stream_from_another_thread_ends_the_read(self):
        fake = self.service(reply="一\n二\n三\n四\n五\n六\n七\n八\n", delay=0.3)
        stream = self.llm.OpenAICompatible(fake.url).stream("數到八", "m")
        threading.Timer(0.5, stream.close).start()                     # as the answer's watchdog does
        t0 = time.time()
        got = list(stream)
        self.assertLess(time.time() - t0, 2)
        self.assertLess(len(got), 8)
        stream.close()                                                 # twice is fine

    def test_closing_a_stream_ends_a_read_that_waits_on_a_stalled_service(self):
        # Closing the reply alone waited for the read to time out (60 s): the answer's watchdog fired at 90 s and
        # the worker, with the notes' stream lock, was held for up to 150 s.
        fake = self.service(reply="一\n二\n")
        fake.stall = True
        stream = self.llm.OpenAICompatible(fake.url).stream("數到二", "m")
        threading.Timer(0.5, stream.close).start()
        t0 = time.time()
        got = list(stream)
        self.assertLess(time.time() - t0, 3)
        self.assertEqual(got, ["一\n"])

    def test_a_refused_key_is_remembered_until_a_call_works(self):
        fake = self.service(status=401)
        model = self.llm.OpenAICompatible(fake.url, key="wrong", model="m")
        with self.assertRaises(self.llm.LLMError) as failed:
            model.complete("hi", "m")
        self.assertIn("(401)", str(failed.exception))
        self.assertEqual(model.problem(), "rejected")
        with self.assertRaises(self.llm.LLMError):
            model.stream("hi", "m")
        fake.status = 200
        model.complete("hi", "m")
        self.assertIsNone(model.problem())

    def test_a_wrong_key_shows_before_the_first_meeting_and_clears_once_mended(self):
        # No call is made outside a meeting: the service is asked which models it has, which costs nothing.
        fake = self.service(status=401)
        model = self.llm.OpenAICompatible(fake.url, key="wrong", model="m")
        self.assertEqual(model.problem(), "rejected")
        self.assertEqual((fake.probes, fake.requests), (["/v1/models"], []))
        fake.status = 200                                              # the user mended the key
        self.assertIsNone(model.problem())
        self.assertIsNone(model.problem())
        self.assertEqual(len(fake.probes), 2)                          # asked while in trouble, not for ever after
        quiet = self.service()
        quiet.has_models = False                                       # a service without that list says nothing
        self.assertIsNone(self.llm.OpenAICompatible(quiet.url, model="m").problem())

    def test_calls_that_keep_failing_show_as_a_problem_for_a_while(self):
        # A wrong model name, a service that is down, a quota used up: only "openai failed" lines in the log before.
        fake = self.service(status=404)
        model = self.llm.OpenAICompatible(fake.url, key="k", model="no-such-model")
        self.assertIsNone(model.problem())
        for _ in range(2):
            with self.assertRaises(self.llm.LLMError):
                model.complete("hi", "no-such-model")
        self.assertEqual(model.problem(), "failing")
        self.assertIn("(404)", model.last_error)
        model.failed_at -= model.FAILING_SECS + 1                      # long after the meeting: not stuck there
        self.assertIsNone(model.problem())
        fake.status = 403                                              # a region, a model the key may not use...
        for _ in range(2):
            with self.assertRaises(self.llm.LLMError):
                model.complete("hi", "m")
        self.assertEqual(model.problem(), "failing")                   # ... is not "the key was refused"
        fake.status = 200
        model.complete("hi", "m")
        self.assertIsNone(model.problem())

    def test_other_failures_say_why(self):
        fake = self.service(status=500)
        model = self.llm.OpenAICompatible(fake.url, model="m")
        with self.assertRaises(self.llm.LLMError) as failed:
            model.complete("hi", "m")
        self.assertIn("(500)", str(failed.exception))
        self.assertIsNone(model.problem())                             # the service's trouble, not the key's
        fake.status = 200
        for body in (b"not json", b'{"choices": []}', b'{"error": "x"}'):
            fake.body = body
            with self.assertRaises(self.llm.LLMError):
                model.complete("hi", "m")
        fake.close()
        self.servers.remove(fake)
        with self.assertRaises(self.llm.LLMError):                     # nobody listens there any more
            model.complete("hi", "m", timeout=5)
        with self.assertRaises(self.llm.LLMError):
            self.llm.OpenAICompatible("").complete("hi", "m")

    def test_what_is_missing_is_named(self):
        self.assertEqual(self.llm.OpenAICompatible("", model="m").problem(), "no_url")
        self.assertEqual(self.llm.OpenAICompatible("http://localhost:1/v1", model="").problem(), "no_model")


class Choice(Base):
    def test_claude_is_the_default_with_a_model_per_job(self):
        provider, models = self.llm.from_config({}, "/usr/local/bin/claude")
        self.assertEqual((provider.name, provider.path, provider.web, provider.vision), ("claude", "/usr/local/bin/claude", True, True))
        self.assertEqual(models, {"notes": "claude-sonnet-5-5", "check": "claude-haiku-5-5", "answer": "claude-sonnet-5-5",
                                  "verify": "claude-sonnet-5-5", "screen": "claude-sonnet-5-5"})
        _, models = self.llm.from_config({"copilot": {"model": "a"}, "explain": {"model": "b", "answer_model": "c"},
                                          "screen": {"speaker_model": "d"}}, "claude")
        self.assertEqual(models, {"notes": "a", "check": "b", "answer": "c", "verify": "a", "screen": "d"})

    def test_another_service_uses_the_users_own_models_and_key(self):
        key_file = self.sb.home / "key"
        key_file.write_text("sk-from-file\nsecond line is not the key\n")
        cfg = {"llm": {"provider": "openai", "base_url": "https://example.test/v1", "model": "big", "fast_model": "small",
                       "vision": True, "api_key_file": str(key_file)},
               "explain": {"answer_model": "claude-sonnet-5-5"}}           # Claude's names are not sent elsewhere
        provider, models = self.llm.from_config(cfg, "claude")
        self.assertEqual((provider.name, provider.base_url, provider.key, provider.vision, provider.web),
                         ("openai", "https://example.test/v1", "sk-from-file", True, False))
        self.assertEqual(models, {"notes": "big", "check": "small", "answer": "big", "verify": "big", "screen": "big"})
        self.assertIsNone(provider.problem())
        cfg["llm"].pop("fast_model")
        cfg["llm"]["api_key_file"] = str(self.sb.home / "no-such-file")
        provider, models = self.llm.from_config(cfg, "claude")
        self.assertEqual((models["check"], provider.key, provider.problem()), ("big", "", None))
        import os
        os.environ["MEETING_COPILOT_API_KEY"] = " sk-from-env "
        try:
            self.assertEqual(self.llm.from_config(cfg, "claude")[0].key, "sk-from-env")
        finally:
            del os.environ["MEETING_COPILOT_API_KEY"]
        self.assertEqual(self.llm.from_config({"llm": {"provider": "openai"}}, "claude")[0].problem(), "no_url")
        self.assertEqual(self.llm.from_config({"llm": {"provider": " OpenAI"}}, "claude")[0].name, "openai")

    def test_a_provider_that_is_neither_sends_nothing_anywhere(self):
        # "ollama" with a local address used to fall back to Claude: the meeting went to the Claude account.
        provider, models = self.llm.from_config({"llm": {"provider": "ollama", "base_url": "http://localhost:11434/v1"}},
                                                str(FAKES_BIN / "claude"))
        self.assertEqual((provider.name, provider.problem(), provider.vision, provider.web), ("none", "unknown_provider", False, False))
        for call in (provider.complete, provider.stream):
            with self.assertRaises(self.llm.LLMError):
                call("hi", models["notes"])
        self.assertEqual(self.sb.claude_calls(), [])

    def test_a_key_file_in_another_encoding_does_not_stop_the_program(self):
        key_file = self.sb.home / "key"
        key_file.write_bytes("sk-test\n".encode("utf-16"))
        self.llm.api_key({"api_key_file": str(key_file)})                 # whatever it reads, it does not raise
        key_file.write_bytes(b"\xef\xbb\xbfsk-test\r\n")                    # saved with a byte-order mark
        self.assertEqual(self.llm.api_key({"api_key_file": str(key_file)}), "sk-test")


class ClaudeCode(Base):
    def setUp(self):
        super().setUp()
        self.claude = self.llm.ClaudeCLI(str(FAKES_BIN / "claude"))

    def test_replies_and_failures(self):
        self.sb.flag("window-reply", text="1 Q\n")
        self.assertEqual(self.claude.complete("[其他人 A] 這是什麼", "fake").strip(), "1 Q")
        self.assertIn("items", self.claude.complete("整理一下", "fake", images=[IMAGE]))
        self.assertEqual(self.sb.claude_calls()[-1]["images"], 1)
        self.assertIn("items", self.claude.complete("整理一下", "fake", as_message=True))
        pieces = list(self.claude.stream("[其他人 A] 什麼是 gRPC", "fake"))
        self.assertIn("問題\n", pieces)
        self.sb.flag("claude-fail")
        for call in (lambda: self.claude.complete("x", "fake"), lambda: self.claude.complete("x", "fake", as_message=True)):
            with self.assertRaises(self.llm.LLMError):
                call()
        with self.assertRaises(self.llm.LLMError):
            self.llm.ClaudeCLI("/nonexistent/claude").complete("x", "fake")

    def test_a_stream_can_be_stopped(self):
        stream = self.claude.stream("[其他人 A] 什麼是 gRPC", "fake")
        reader = iter(stream)
        next(reader)
        stream.close()
        self.assertIsNotNone(stream.proc.poll())
        self.assertEqual(list(reader), [])                             # closed under the reader: it ends, no error

    def test_sign_in(self):
        self.assertIsNone(self.claude.problem())
        self.sb.flag("claude-signed-out")
        self.assertEqual(self.claude.problem(), "signed_out")
        self.assertEqual(self.llm.ClaudeCLI("/nonexistent/claude").problem(), "missing")
        self.assertEqual(self.llm.ClaudeCLI("").problem(), "missing")


if __name__ == "__main__":
    unittest.main()
