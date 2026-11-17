"""Speech segmentation and transcription shared by the live pipeline and offline tools.

- Silero VAD cuts audio into speech segments (<= MAX_SEGMENT_SECS).
- The primary model for the configured language transcribes each segment (Traditional Chinese: TEA-ASR-1.1,
  Taiwan Traditional; Simplified Chinese: Qwen3-ASR-1.7B, its output put in Simplified script (t2s, the script
  only) with numerals normalized; English: Qwen3-ASR; see config.AUTO_PRIMARY).
- Guards: output-length cap, private-use-character stripping, repetition and truncation
  detection, hallucination filter (lib/text.py). A segment that fails the guards is retried with
  the fallback model (Traditional Chinese: Qwen3-ASR converted to Taiwan Traditional with numerals normalized;
  Simplified Chinese: Qwen3-ASR-0.6B, as the primary; English: Parakeet).
"""
import re
from dataclasses import dataclass
from pathlib import Path

import mlx.core as mx
import numpy as np
import opencc
from mlx_audio.stt.utils import load_model
from silero_vad import get_speech_timestamps, load_silero_vad

SAMPLE_RATE = 16000
# Long speech is cut into lines the notes can show while the person still talks. Live, a line is cut at the first
# pause (PAUSE_SECS of no speech) once it is SOFT_SEGMENT_SECS long, and at MAX_SEGMENT_SECS whatever. Replayed on
# a recorded half-hour call: a word waited 4.5 s on average to show (6.4 s with a 30 s cut, no pause rule) and
# 18 s at worst (33 s); no line was cut mid-word (a hard 15 s cut cut 24).
MAX_SEGMENT_SECS = 20
SOFT_SEGMENT_SECS = 10
PAUSE_SECS = 0.1
SEGMENT_PAD_SECS = 0.2
TOKENS_PER_SEC = 12          # Mandarin speech is ~4-6 characters/s; leave headroom for English
MIN_TOKENS = 24


def memory_report():
    """MLX memory so far: peak, in use and kept for reuse (MB)."""
    if not hasattr(mx, "get_peak_memory"):
        return "mlx memory: unknown"
    mb = lambda n: round(n / 2**20)  # noqa: E731
    return (f"mlx memory: peak {mb(mx.get_peak_memory())} MB, active {mb(mx.get_active_memory())} MB, "
            f"cache {mb(mx.get_cache_memory())} MB")


PUA = re.compile(r"[-]")
CN_DIGITS = {"零": "0", "〇": "0", "一": "1", "二": "2", "三": "3", "四": "4",
             "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
CN_DIGIT_RUN = re.compile(r"[零〇一二三四五六七八九]{3,}")   # e.g. 二零一六 -> 2016; shorter runs are often words
from text import HALLUCINATION_EXACT, HALLUCINATION_MARKERS, is_hallucination  # noqa: F401 (shared filter)
ASR_LANGUAGE = {"zh": "Chinese", "zh-hans": "Chinese", "en": "English"}


@dataclass
class Segment:
    start: float        # seconds from the start of the track
    end: float
    text: str
    engine: str         # "primary", "fallback" or "dropped"
    note: str = ""


def is_repetitive(text, min_repeats=5):
    """True if a 1-10 character unit repeats back-to-back min_repeats times (Qwen3 'endless repetition')."""
    compact = re.sub(r"\s+", "", text)
    return bool(re.search(r"(.{1,10}?)\1{%d,}" % (min_repeats - 1), compact))


TOKEN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9]+")
TRUNCATED_MIN_SECS = 5.0
TRUNCATED_RATE = 1.5     # characters or words per second; speech is ~4-6, so far below means words were lost


def looks_truncated(text, seconds):
    """A long segment that came back with very few words: the model dropped part of it
    (seen in testing: a 10 s segment came back as 8 characters)."""
    return seconds >= TRUNCATED_MIN_SECS and len(TOKEN.findall(text)) < seconds * TRUNCATED_RATE


# Echo of call audio leaking into the user's microphone (measured on a recorded call):
# user's own speech mic/system RMS >= 6.5 (call silent); echo 0.12-0.30.
# 0.45 leaves room for the user talking over others (mic = own voice + echo).
ECHO_RATIO = 0.45
CALL_ACTIVE_RMS = 0.005


def rms(audio):
    return float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0


def is_echo(mic_rms, call_rms):
    """A mic segment is call echo if the call was active and the mic was much quieter than the call."""
    return call_rms > CALL_ACTIVE_RMS and mic_rms < ECHO_RATIO * call_rms


class SelfLevelGate:
    """Keep only microphone speech loud enough to be the user, learned per meeting.

    Nearby people reach the mic much quieter than the user (in one test meeting:
    user 0.04-0.07 RMS, people nearby 0.02-0.03). Levels depend on the microphone, so
    the threshold is learned from the meeting's own mic segments: split log-RMS into a
    loud and a quiet group; if they are clearly apart the threshold sits between them,
    otherwise everything above a lenient floor passes.
    """

    MIN_SAMPLES = 8
    MIN_SEPARATION = 1.6          # loud group mean / quiet group mean
    DEFAULT = 0.035
    FLOOR, CEILING = 0.008, 0.08

    def __init__(self, fixed=None):
        self.fixed = fixed            # a number from config disables learning
        self.levels = []

    def threshold(self):
        if self.fixed:
            return float(self.fixed)
        if len(self.levels) < self.MIN_SAMPLES:
            return self.DEFAULT
        x = np.log(np.clip(np.array(self.levels[-200:]), 1e-5, None))
        lo, hi = np.percentile(x, 20), np.percentile(x, 80)
        for _ in range(20):           # 1-D two-means on log RMS
            mid = (lo + hi) / 2
            a, b = x[x <= mid], x[x > mid]
            if not len(a) or not len(b):
                break
            lo, hi = a.mean(), b.mean()
        if np.exp(hi - lo) >= self.MIN_SEPARATION:
            t = float(np.exp((lo + hi) / 2))
        elif np.exp((lo + hi) / 2) >= self.DEFAULT:
            t = float(np.exp(lo)) * 0.6   # one loud group: only the user talks, be lenient
        else:
            t = self.DEFAULT              # one quiet group: likely only people nearby so far
        return min(max(t, self.FLOOR), self.CEILING)

    def accept(self, level):
        """Record a mic segment's RMS and say whether it is loud enough to be the user."""
        self.levels.append(level)
        return level >= self.threshold()


def normalize_numbers(text):
    return CN_DIGIT_RUN.sub(lambda m: "".join(CN_DIGITS[c] for c in m.group(0)), text)


def speech_spans(audio, vad=None):
    """Speech spans in seconds, each at most MAX_SEGMENT_SECS long (no ASR model needed)."""
    stamps = get_speech_timestamps(audio, vad or load_silero_vad(onnx=True), sampling_rate=SAMPLE_RATE,
                                   max_speech_duration_s=MAX_SEGMENT_SECS, min_silence_duration_ms=400,
                                   speech_pad_ms=int(SEGMENT_PAD_SECS * 1000), return_seconds=True)
    return [(s["start"], s["end"]) for s in stamps]


def load_corrections(path):
    """(heard, meant) pairs from "heard => meant" lines (# comments): words the models keep mishearing, e.g.
    "pie torch => PyTorch"."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ()
    pairs = []
    for line in lines:
        heard, sep, meant = line.split("#", 1)[0].partition("=>")
        if sep and heard.strip():
            pairs.append((heard.strip(), meant.strip()))
    return tuple(pairs)


def correct(text, pairs):
    """text with each heard phrase replaced; ASCII phrases ignore case and must stand on their own ("py3", not
    "py3k")."""
    for heard, meant in pairs:
        if heard.isascii():
            text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(heard)}(?![A-Za-z0-9])", lambda _m, m=meant: m, text,
                          flags=re.I)
        else:
            text = text.replace(heard, meant)
    return text


class Transcriber:
    def __init__(self, primary=None, fallback=None):
        """Models default to the [asr] section of the meeting-copilot config."""
        import config
        self.cfg = cfg = config.load()
        self.family = config.language_family(cfg)       # "zh" / "zh-hans": Chinese clean-up below; "en": none
        self.primary = load_model(primary or config.asr_model(cfg, "primary_model"))
        self._fallback_id = fallback or config.asr_model(cfg, "fallback_model")
        self._fallback = None
        self.fallback_error = None          # why the fallback could not be loaded (it is then not retried)
        self._s2twp = opencc.OpenCC("s2twp")
        self._t2s = opencc.OpenCC("t2s")     # Simplified: the script only, never the words that were said
        self.vad = load_silero_vad(onnx=True)
        self.corrections = load_corrections(config.asr_corrections_file(cfg))

    @property
    def fallback(self):
        """The fallback model, loaded on first use; None when it cannot be loaded (not installed, and the live
        transcriber does not download during a meeting)."""
        if self._fallback is None and self.fallback_error is None:
            try:
                self._fallback = load_model(self._fallback_id)
            except Exception as e:  # noqa: BLE001
                self.fallback_error = f"{type(e).__name__}: {e}"[:200]
        return self._fallback

    def speech_spans(self, audio):
        return speech_spans(audio, self.vad)

    def _run(self, model, audio, seconds):
        max_tokens = max(MIN_TOKENS, int(seconds * TOKENS_PER_SEC))
        result = model.generate(mx.array(audio), language=ASR_LANGUAGE[self.family], max_tokens=max_tokens)
        return getattr(result, "text", str(result)).strip(), max_tokens

    corrections = ()                  # (heard, meant) pairs: load_corrections()

    def transcribe_segment(self, audio, start, end):
        seg = self._transcribe_segment(audio, start, end)
        seg.text = correct(seg.text, self.corrections)
        return seg

    def _transcribe_segment(self, audio, start, end):
        seconds = len(audio) / SAMPLE_RATE
        text, _ = self._run(self.primary, audio, seconds)
        text = PUA.sub("", text).strip()
        if self.family == "zh-hans":  # Qwen3 spells digits out; a Traditional model (TEA) set by hand writes 繁體
            text = normalize_numbers(self._t2s.convert(text))
        truncated = looks_truncated(text, seconds)
        if not is_repetitive(text) and not is_hallucination(text) and not truncated:
            return Segment(start, end, text, "primary")
        reason = "repetition" if is_repetitive(text) else "hallucination" if is_hallucination(text) else "too short"
        if self.fallback is None:            # no second opinion: keep a merely short result, drop junk
            if not is_repetitive(text) and not is_hallucination(text) and text:
                return Segment(start, end, text, "primary", f"primary {reason}; fallback unavailable")
            return Segment(start, end, "", "dropped", f"primary {reason}; fallback unavailable")
        retry, _ = self._run(self.fallback, audio, seconds)
        if self.family == "zh":       # Qwen3 writes Simplified Chinese and spelled-out digits
            retry = normalize_numbers(self._s2twp.convert(retry))
        elif self.family == "zh-hans":
            retry = normalize_numbers(self._t2s.convert(retry))
        retry = retry.strip()
        if truncated and not is_repetitive(text) and not is_hallucination(text) \
                and len(TOKEN.findall(retry)) <= len(TOKEN.findall(text)):
            return Segment(start, end, text, "primary")            # the fallback found no more: keep it
        if retry and not is_repetitive(retry) and not is_hallucination(retry):
            return Segment(start, end, retry, "fallback", f"primary {reason}")
        return Segment(start, end, "", "dropped", f"primary {reason}; fallback rejected")

    def transcribe(self, audio, offset=0.0):
        """Segment a mono 16 kHz float32 array with VAD and transcribe each speech span."""
        out = []
        for s, e in self.speech_spans(audio):
            chunk = audio[int(s * SAMPLE_RATE): int(e * SAMPLE_RATE)]
            if len(chunk) < SAMPLE_RATE * 0.3:
                continue
            out.append(self.transcribe_segment(chunk, offset + s, offset + e))
        return out


def load_track(path):
    import soundfile as sf
    audio, sr = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != SAMPLE_RATE:
        raise ValueError(f"{path}: expected {SAMPLE_RATE} Hz, got {sr}")
    return np.ascontiguousarray(audio)
