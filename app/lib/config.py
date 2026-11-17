"""Load meeting-copilot settings: defaults < config.example.toml < ~/.config/meeting-copilot/config.toml.

Nothing here is user-specific; paths are derived from the install location or the config file.
"""
import os
import shutil
import tomllib
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]          # .../app
EXAMPLE = APP_DIR / "config.example.toml"
USER_CONFIG = Path(os.environ.get("MEETING_COPILOT_CONFIG",
                                  Path.home() / ".config" / "meeting-copilot" / "config.toml"))


def _merge(base, override):
    out = dict(base)
    for key, value in override.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


LOAD_ERROR = None      # why the user's config was ignored, for the status file


def load():
    """Merged settings. A user config that cannot be parsed is ignored (and reported through
    LOAD_ERROR) rather than crashing every program that starts, which launchd would keep restarting."""
    global LOAD_ERROR
    cfg = tomllib.loads(EXAMPLE.read_text()) if EXAMPLE.exists() else {}
    if USER_CONFIG.exists():
        try:
            cfg = _merge(cfg, tomllib.loads(USER_CONFIG.read_text(encoding="utf-8")))
            LOAD_ERROR = None
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as e:
            LOAD_ERROR = f"{USER_CONFIG.name}: {e}; using the defaults until it is fixed"
    return cfg


def expand(path):
    return Path(os.path.expanduser(path)) if path else None


def models_dir():
    """The installer's models (MLX conversions, speaker model): app/models, or MEETING_COPILOT_MODELS (tests)."""
    return Path(os.environ.get("MEETING_COPILOT_MODELS") or APP_DIR / "models")


def state_dir():
    """Runtime state (status, logs, live transcripts): app/state, or MEETING_COPILOT_STATE (tests)."""
    return Path(os.environ.get("MEETING_COPILOT_STATE") or APP_DIR / "state")


def tool(name, default):
    """Path of a helper or system command. MEETING_COPILOT_BIN names a folder whose entries replace
    them one by one (tests run the engine against fake `minutes`, `claude`, `launchctl`...)."""
    override = os.environ.get("MEETING_COPILOT_BIN")
    if override and (Path(override) / name).exists():
        return str(Path(override) / name)
    return str(default)


def meetings_dir(cfg):
    return expand(cfg.get("paths", {}).get("meetings_dir") or "~/meetings")


def minutes_app(cfg):
    return expand(cfg.get("paths", {}).get("minutes_app") or "/Applications/Minutes.app")


def known_terms_file(cfg):
    """Terms never explained: MEETING_COPILOT_KNOWN_TERMS, else [explain] known_terms_file, else default."""
    return expand(os.environ.get("MEETING_COPILOT_KNOWN_TERMS") or cfg.get("explain", {}).get("known_terms_file")
                  or "~/.config/meeting-copilot/known_terms.txt")


def asr_corrections_file(cfg):
    """Words speech recognition mishears ("heard => meant" per line): [asr] corrections_file, else default."""
    return expand(cfg.get("asr", {}).get("corrections_file") or "~/.config/meeting-copilot/asr_corrections.txt")


def claude_cli(cfg):
    """The user's own Claude Code CLI: configured path, else PATH, else the default install location."""
    configured = expand(cfg.get("paths", {}).get("claude_cli", ""))
    if configured and configured.exists():
        return str(configured)
    found = shutil.which("claude")
    if found:
        return found
    default = Path.home() / ".local" / "bin" / "claude"
    return str(default) if default.exists() else "claude"


# Speech-recognition defaults per language family for primary_model / fallback_model = "auto".
# Benchmarks: Chinese (reports/台灣華語語音辨識實測評比.md), TEA best on Taiwanese Mandarin. English (AMI,
# Earnings-22 and LibriSpeech samples; no report in this repository), Qwen3 and Parakeet v2 tied at 19.5 / 19.4 %
# WER, Whisper large-v3 24 %, TEA unstable on far-field English. The fallback is a different model, so a stuck or
# hallucinating primary gets a real second opinion.
# Chinese fallback: Qwen3-ASR-0.6B (13.0 % vs 11.7 % for 1.7B in the benchmark, 2.4x faster, ~1.5 GB less).
# 1.7B next to TEA (itself Qwen3-ASR-1.7B underneath) held two copies of the same size, 5.3 GB in all; on a
# Mac with less memory, during a browser call, that pushed the system into swap, and the fallback answers only a small share
# of lines.
# Simplified Chinese (mainland Mandarin): Qwen3-ASR, which writes Simplified Chinese itself; TEA is Qwen3-ASR-1.7B
# fine-tuned on Taiwanese Mandarin. Not benchmarked on mainland speech here.
AUTO_PRIMARY = {"zh": "JacobLinCool/TEA-ASR-1.1", "zh-hans": "mlx-community/Qwen3-ASR-1.7B-8bit",
                "en": "mlx-community/Qwen3-ASR-1.7B-8bit"}
AUTO_FALLBACK = {"zh": "mlx-community/Qwen3-ASR-0.6B-8bit", "zh-hans": "mlx-community/Qwen3-ASR-0.6B-8bit",
                 "en": "mlx-community/parakeet-tdt-0.6b-v2"}


def language_family(cfg):
    """"zh", "zh-hans" or "en" from [user] language, or from macOS when it is empty."""
    import i18n
    return i18n.family(cfg.get("user", {}).get("language") or i18n.macos_language())


def asr_model(cfg, key):
    """A local model path, the installer's MLX 8-bit conversion in app/models/, or a Hugging Face id."""
    value = cfg.get("asr", {}).get(key, "")
    if value == "auto":
        value = (AUTO_FALLBACK if key == "fallback_model" else AUTO_PRIMARY)[language_family(cfg)]
    local = expand(value)
    if local and local.exists():
        return str(local)
    converted = models_dir() / f"{value.split('/')[-1]}-8bit"
    return str(converted) if converted.exists() else value


def asr_model_missing(cfg, key="primary_model"):
    """The model id when asr_model() would have to fetch it mid-meeting (e.g. the language changed after
    install.sh ran), else "". A Hugging Face id counts as installed once it is in the local cache."""
    model = asr_model(cfg, key)
    if expand(model).exists():
        return ""
    if model.startswith(("/", "~", ".")) or model.count("/") != 1:
        return model                     # a configured folder that is gone, not a Hugging Face id
    try:
        from huggingface_hub import try_to_load_from_cache
        cached = try_to_load_from_cache(model, "config.json")
    except ImportError:
        return ""
    except Exception:  # noqa: BLE001 - runs when the service starts: it must never stop it
        return model
    return "" if isinstance(cached, str) else model


def minutes_cli(cfg):
    return str(minutes_app(cfg) / "Contents" / "MacOS" / "minutes")
