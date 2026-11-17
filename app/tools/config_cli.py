"""Read and change the meeting-copilot config for the Mac app's settings window.

  config_cli.py get                         merged settings (defaults + yours) as JSON, plus microphones
  config_cli.py set '<json>'                {"section.key": value, ...} into your config.toml
  config_cli.py set-key                     the model service's key, read from stdin, into [llm] api_key_file
                                            (only you can read the file; an empty stdin removes it)
  config_cli.py llm-test ['<json>'] [--key-stdin]
                                            ask the model for one word, with the saved [llm] settings or with
                                            {"key": value} of [llm] in their place (--key-stdin: a key on stdin
                                            instead of the saved one); prints {"ok": true, "reply": ...} or
                                            {"ok": false, "error": ...}

Edits keep every other line and comment of ~/.config/meeting-copilot/config.toml as they are.
After a change that Minutes also needs ([audio], [privacy], [user] language) it re-runs
setup_minutes.py; restarting the background program is the caller's job.
"""
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "lib"))
sys.path.insert(0, str(APP / "tools"))
import config  # noqa: E402
import llm  # noqa: E402
import tomledit  # noqa: E402

MINUTES_SECTIONS = ("audio.", "privacy.", "user.language")
set_value = tomledit.set_key          # kept under its old name for the tests


def microphones():
    try:
        out = subprocess.run([config.minutes_cli(config.load()), "devices"], capture_output=True, text=True,
                             timeout=20).stdout
        # The listing is human text first, then a JSON array on its own lines.
        data = json.loads(out[out.index("\n[") + 1: out.rindex("]") + 1])
        return [re.sub(r"\s*\(\d+Hz, \d+ ch\)$", "", d) for d in data]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def key_file(cfg):
    return Path(cfg.get("llm", {}).get("api_key_file") or llm.DEFAULT_KEY_FILE).expanduser()


def set_key(key):
    path = key_file(config.load())
    if not key:
        path.unlink(missing_ok=True)
        return
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:     # never readable by others
        f.write(key + "\n")
    os.replace(tmp, path)


def llm_test(overrides, key):
    """One short question to the model the settings name: does the address answer, is the key taken, is there
    such a model? The reply's first words come back so the user sees it is their model talking."""
    cfg = config.load()
    cfg["llm"] = {**cfg.get("llm", {}), **overrides}
    provider, models = llm.from_config(cfg, config.claude_cli(cfg))
    if key and hasattr(provider, "key"):
        provider.key = key
    problem = provider.problem()
    if problem:
        return {"ok": False, "error": problem}
    reply = ""
    for model in dict.fromkeys((models["answer"], models["check"])):     # the faster model too, when there is one
        try:
            answer = provider.complete("Reply with the single word OK.", model, timeout=45)
        except llm.LLMError as e:
            return {"ok": False, "error": str(e)[:300], "model": model}
        reply = reply or answer
    return {"ok": True, "reply": reply.strip()[:80], "model": models["answer"]}


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "get":
        cfg = config.load()
        print(json.dumps({"config": cfg, "microphones": microphones(), "api_key_set": key_file(cfg).exists()},
                         ensure_ascii=False))
    elif len(sys.argv) == 2 and sys.argv[1] == "set-key":
        set_key(sys.stdin.read().strip())
        print(json.dumps({"ok": True}))
    elif len(sys.argv) >= 2 and sys.argv[1] == "llm-test":
        rest = [a for a in sys.argv[2:] if a != "--key-stdin"]
        key = sys.stdin.read().strip() if "--key-stdin" in sys.argv[2:] else ""
        print(json.dumps(llm_test(json.loads(rest[0]) if rest else {}, key), ensure_ascii=False))
    elif len(sys.argv) == 3 and sys.argv[1] == "set":
        changes = json.loads(sys.argv[2])
        path = config.USER_CONFIG
        text = path.read_text() if path.exists() else ""
        for dotted, value in changes.items():
            section, key = dotted.rsplit(".", 1)
            text = set_value(text, section, key, value)
        try:
            tomllib.loads(text)            # never write a file the background program can't read
        except tomllib.TOMLDecodeError as e:
            sys.exit(f"the edited config would be invalid ({e}); nothing was written")
        import os
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)              # the background program never reads a half-written file
        if any(k.startswith(MINUTES_SECTIONS) for k in changes):
            subprocess.run([sys.executable, str(APP / "tools" / "setup_minutes.py")], check=False)
        print(json.dumps({"ok": True, "changed": list(changes)}))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
