"""load_model(name) -> FakeModel. Behaviour, per model name and audio level:
  - silence (RMS < 0.002): a known hallucination ("謝謝觀看") so the engine's filter has something to catch
  - a name containing "repeat": "哈哈哈哈哈哈哈哈" (repetition guard)
  - otherwise "<prefix> 第 n 句" / "<prefix> sentence n", n counting per model; Chinese or English by `language`
MC_FAKE_ASR_LOG (a file path) records every call as one JSON line."""
import json
import os
from dataclasses import dataclass

import numpy as np


@dataclass
class Result:
    text: str


class FakeModel:
    def __init__(self, name):
        self.name = name
        self.calls = 0

    def generate(self, audio, language="Chinese", max_tokens=None, **kw):
        self.calls += 1
        a = np.asarray(audio, dtype=np.float32)
        rms = float(np.sqrt(np.mean(np.square(a)))) if a.size else 0.0
        if rms < 0.002:
            text = "謝謝觀看" if language == "Chinese" else "thanks for watching"
        elif "repeat" in self.name:
            text = "哈哈哈哈哈哈哈哈哈哈"
        else:
            prefix = self.name.rsplit("/", 1)[-1]
            text = f"{prefix} 第 {self.calls} 句" if language == "Chinese" else f"{prefix} sentence {self.calls}"
        log = os.environ.get("MC_FAKE_ASR_LOG")
        if log:
            with open(log, "a") as f:
                f.write(json.dumps({"model": self.name, "seconds": round(a.size / 16000, 2), "rms": round(rms, 4),
                                    "language": language, "max_tokens": max_tokens, "text": text},
                                   ensure_ascii=False) + "\n")
        return Result(text)


def load_model(name, **kw):
    if "missing" in str(name):
        raise FileNotFoundError(f"fake model not found: {name}")
    return FakeModel(str(name))
