"""One door to the model that writes the notes and the answers ([llm] in the config).

Two kinds of provider:
- "claude": the user's own Claude subscription, through their Claude Code command-line program (the default).
- "openai": any service that speaks the OpenAI chat-completions format: DeepSeek, Qwen, OpenAI, Claude's own API, a
  model on this Mac (Ollama, LM Studio)... The user gives its address, key and model names. Which model, and how well
  it does, is theirs to choose: nothing here is tuned to one.

Both give complete() for one reply, stream() for a reply as it is written, and problem() for why it cannot run.
"""
import http.client
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

# Claude runs from a folder outside any repository, with its own system prompt instead of Claude
# Code's: with the default one (and a repository around it) the model acts as a coding agent and
# writes tool calls into answers. Tools are Claude Code's defaults;
# web search and fetch are pre-approved where an answer may be checked online.
SANDBOX = Path.home() / "Library" / "Caches" / "meeting-copilot" / "claude-cwd"
SYSTEM_PROMPT = ("You are a live meeting assistant built into a meeting app, not a coding assistant: this is not a "
                 "software task. Follow the instructions in the user's message exactly and output only what they "
                 "ask for.")
BASE_ARGS = ["--system-prompt", SYSTEM_PROMPT, "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
             "--disable-slash-commands", "--no-session-persistence"]
WEB_TOOLS = ["--allowedTools", "WebSearch", "WebFetch"]
KEY_ENV = "MEETING_COPILOT_API_KEY"
USER_AGENT = "meeting-copilot"
DEFAULT_KEY_FILE = "~/.config/meeting-copilot/api_key"


def sandbox():
    SANDBOX.mkdir(parents=True, exist_ok=True)
    return str(SANDBOX)


class LLMError(Exception):
    """The model gave no reply: the text says why (for the log)."""


class ClaudeCLI:
    """The user's Claude subscription, through Claude Code."""

    name = "claude"
    web = True         # can search the web (the background check of an answer, the notes)
    vision = True      # reads the screen frames

    def __init__(self, path):
        self.path = path

    def complete(self, prompt, model, images=(), web=False, timeout=180, as_message=False):
        """One reply. `images` are Anthropic image blocks; with them (or as_message) the prompt goes in as a
        message on stdin and the reply is read from the result event, as the notes have always done."""
        tools = WEB_TOOLS if web else []
        try:
            if images or as_message:
                message = json.dumps({"type": "user", "message": {
                    "role": "user", "content": [*images, {"type": "text", "text": prompt}]}})
                res = subprocess.run(
                    [self.path, "-p", "--model", model, *BASE_ARGS, *tools, "--input-format", "stream-json",
                     "--output-format", "stream-json", "--verbose"],
                    input=message + "\n", capture_output=True, text=True, timeout=timeout, cwd=sandbox())
                for line in reversed(res.stdout.splitlines()):
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("type") == "result":
                        if event.get("is_error") or res.returncode != 0:
                            break
                        return event.get("result", "")
                raise LLMError((res.stdout + res.stderr)[-300:])
            res = subprocess.run([self.path, "-p", "--model", model, *BASE_ARGS, *tools, "--", prompt],
                                 capture_output=True, text=True, timeout=timeout, cwd=sandbox())
        except (subprocess.TimeoutExpired, OSError) as e:
            raise LLMError(repr(e)) from e
        if res.returncode != 0:
            raise LLMError(f"({res.returncode}): {(res.stderr or res.stdout)[-200:]}")
        return res.stdout or ""

    def stream(self, prompt, model):
        return _CLIStream(subprocess.Popen(
            [self.path, "-p", "--model", model, "--output-format", "stream-json", "--include-partial-messages",
             "--verbose", *BASE_ARGS, "--", prompt],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=sandbox()))

    def problem(self):
        """None when it can run, else "missing" or "signed_out"."""
        if not self.path or not Path(self.path).exists():
            return "missing"
        try:
            res = subprocess.run([self.path, "auth", "status"], capture_output=True, text=True, timeout=20)
            if json.loads(res.stdout).get("loggedIn"):
                return None
        except (subprocess.TimeoutExpired, OSError, ValueError, AttributeError):
            pass
        return "signed_out"


class _CLIStream:
    """Claude Code's streamed reply: the text as it is written. close() stops it (safe from another thread)."""

    def __init__(self, proc):
        self.proc = proc

    def __iter__(self):
        lines = iter(self.proc.stdout)
        while True:
            try:
                raw = next(lines)
            except (StopIteration, ValueError, OSError):      # the end, or closed under the reader (the watchdog)
                return
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            delta = event.get("event", {}).get("delta", {}) if event.get("type") == "stream_event" else {}
            if delta.get("type") == "text_delta":
                yield delta["text"]

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        self.proc.stdout.close()


class OpenAICompatible:
    """Any service that speaks the OpenAI chat-completions format."""

    name = "openai"
    web = False        # the format has no web search of its own: answers are not checked online
    FAILING_AFTER = 2  # calls failed in a row before it is shown as a problem (one slow reply is not one)
    FAILING_SECS = 900 # ... and how long after the last failed call: no call is made outside a meeting, so the
                       # problem would otherwise stand until the next one, mended or not

    def __init__(self, base_url, key="", vision=False, model="?"):
        self.base_url = (base_url or "").rstrip("/")
        self.key = key
        self.model = model         # only to say so when none is set
        self.vision = vision       # the user says whether their model reads images
        self.rejected = False      # the service refused the key (401) on the last call
        self.failures = 0          # calls failed in a row, for any reason
        self.failed_at = 0.0
        self.last_error = ""
        self.probed = False        # the service was asked once whether it takes the key (problem())

    def _messages(self, prompt, images):
        content = prompt
        if images and self.vision:
            content = [{"type": "image_url", "image_url": {
                "url": f"data:{b['source']['media_type']};base64,{b['source']['data']}"}} for b in images]
            content.append({"type": "text", "text": prompt})
        return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]

    def _headers(self):
        # Named: some services turn the standard library's own name away (403) before they look at the key.
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        return headers

    def _failed(self, detail, code=None):
        self.rejected = code == 401            # 403 is also a region or a model the key may not use: not "the key"
        self.failures += 1
        self.failed_at = time.time()
        self.last_error = detail
        return LLMError(detail)

    def _open(self, model, prompt, images, stream, timeout):
        if not self.base_url:
            raise LLMError("no address: set [llm] base_url")
        body = json.dumps({"model": model, "messages": self._messages(prompt, images), "stream": stream}).encode()
        try:
            request = urllib.request.Request(self.base_url + "/chat/completions", data=body, headers=self._headers())
            response = urllib.request.urlopen(request, timeout=timeout)       # noqa: S310 - the user's own address
        except urllib.error.HTTPError as e:
            detail = e.read(300).decode("utf-8", "replace")
            e.close()
            raise self._failed(f"({e.code}): {detail}", e.code) from e
        except (OSError, ValueError, http.client.HTTPException) as e:        # refused, timed out, bad address
            raise self._failed(repr(e)) from e
        self.rejected, self.failures = False, 0
        return response

    def complete(self, prompt, model, images=(), web=False, timeout=180, as_message=False):
        with self._open(model, prompt, images, False, timeout) as response:
            try:
                reply = json.loads(response.read())
                return reply["choices"][0]["message"]["content"] or ""
            except (OSError, ValueError, KeyError, IndexError, TypeError, http.client.HTTPException) as e:
                raise self._failed(f"unreadable reply: {e!r}") from e

    def stream(self, prompt, model):
        return _SSEStream(self._open(model, prompt, (), True, 60))

    def _probe(self):
        """The service's answer to "which models are there?" with this key: its status code (200, 401...), or None
        when it could not be asked. Free, and most services have it; one that has not (404) says nothing."""
        try:
            request = urllib.request.Request(self.base_url + "/models", headers=self._headers())
            with urllib.request.urlopen(request, timeout=10) as response:     # noqa: S310 - the user's own address
                return response.status
        except urllib.error.HTTPError as e:
            e.close()
            return e.code
        except (OSError, ValueError, http.client.HTTPException):
            return None

    def problem(self):
        """None when it can run, else "no_url", "no_model", "rejected" (the key was refused) or "failing" (calls
        kept failing a moment ago: last_error says how; a wrong model name, a service that is down). The first
        time, and while the key is refused, the service is asked whether it takes the key: so a wrong key shows
        before the first meeting, and clears once it is mended although no meeting is on."""
        if not self.base_url:
            return "no_url"
        if not self.model:
            return "no_model"
        if not self.probed or self.rejected:
            self.probed = True
            code = self._probe()
            if code in (200, 401):
                self.rejected = code == 401
        if self.rejected:
            return "rejected"
        recent = time.time() - self.failed_at < self.FAILING_SECS
        return "failing" if self.failures >= self.FAILING_AFTER and recent else None


class _SSEStream:
    """A streamed chat completion: the text as it is written. A model's reasoning, sent apart from the reply, is
    left out. close() stops it, from another thread too: the connection is shut, so a read that waits on a
    stalled service ends at once (closing the reply alone waits for that read)."""

    def __init__(self, response):
        self.response = response

    def __iter__(self):
        try:
            for raw in self.response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    choices = json.loads(data).get("choices") or [{}]
                    text = (choices[0].get("delta") or {}).get("content")
                except (ValueError, AttributeError, IndexError):
                    continue
                if text:
                    yield text
        except (OSError, ValueError, AttributeError, http.client.HTTPException):
            return                                 # closed under the reader, or the connection dropped

    def close(self):
        try:
            sock = getattr(getattr(getattr(self.response, "fp", None), "raw", None), "_sock", None)
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)
        except (OSError, ValueError, AttributeError):
            pass
        try:
            self.response.close()
        except (OSError, ValueError, AttributeError):
            pass


class NoProvider:
    """[llm] provider names neither kind: nothing is sent anywhere (not to Claude either: the user who wrote
    "ollama" expects the meeting to stay on the Mac)."""

    name = "none"
    web = vision = False

    def complete(self, *args, **kw):
        raise LLMError('[llm] provider must be "claude" or "openai"')

    stream = complete

    def problem(self):
        return "unknown_provider"


def api_key(section):
    """The service's key: the environment's, else the first line of [llm] api_key_file. "" when there is none
    (a model on this Mac needs none)."""
    if os.environ.get(KEY_ENV):
        return os.environ[KEY_ENV].strip()
    try:           # read leniently: a key file in another encoding must not stop the whole program at its start
        path = Path(os.path.expanduser(section.get("api_key_file") or DEFAULT_KEY_FILE))
        return path.read_text(encoding="utf-8", errors="replace").split("\n")[0].strip().lstrip("\ufeff")
    except (OSError, ValueError):
        return ""


def from_config(cfg, claude_path):
    """(provider, models) for this config. models: the model's name for each job: "notes" (the periodic notes),
    "check" (do the new lines hold a question?), "answer" (the instant answer), "verify" (its check online),
    "screen" (who is speaking, read from a frame)."""
    section = cfg.get("llm", {})
    explain, copilot, screen = cfg.get("explain", {}), cfg.get("copilot", {}), cfg.get("screen", {})
    provider = str(section.get("provider", "claude")).strip().lower()
    if provider not in ("claude", "openai"):
        return NoProvider(), dict.fromkeys(("notes", "check", "answer", "verify", "screen"), "")
    if provider == "openai":
        model = section.get("model", "")
        models = {job: model for job in ("notes", "answer", "verify", "screen")}
        models["check"] = section.get("fast_model") or model
        return OpenAICompatible(section.get("base_url", ""), api_key(section), bool(section.get("vision", False)),
                                model), models
    notes = copilot.get("model", "claude-sonnet-5-5")
    return ClaudeCLI(claude_path), {
        "notes": notes, "check": explain.get("model", "claude-haiku-5-5"),
        "answer": explain.get("answer_model", "claude-sonnet-5-5"), "verify": explain.get("verify_model", notes),
        "screen": screen.get("speaker_model", "claude-sonnet-5-5")}
