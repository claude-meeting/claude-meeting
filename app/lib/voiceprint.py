"""Speaker verification: is this microphone segment the user?

A CAM++ (3D-Speaker, zh+en) embedding per segment, compared by cosine similarity with
the user's enrolled voiceprint (mean of embeddings of segments known to be the user).
"""
import json
from pathlib import Path

import numpy as np
import sherpa_onnx

APP = Path(__file__).resolve().parents[1]
MODEL = APP / "models" / "speaker" / "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
SAMPLE_RATE = 16000


class Voiceprint:
    def __init__(self, model=MODEL):
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=2)
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        self.enrolled = None

    def embed(self, audio):
        stream = self.extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, np.ascontiguousarray(audio, dtype=np.float32))
        stream.input_finished()
        v = np.array(self.extractor.compute(stream), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)

    def enroll(self, segments):
        """segments: iterable of float32 arrays known to be the user (>= 1 s each works best)."""
        vecs = [self.embed(a) for a in segments if len(a) >= SAMPLE_RATE]
        if not vecs:
            raise ValueError("no usable enrollment audio (need segments of at least 1 s)")
        m = np.mean(vecs, axis=0)
        self.enrolled = m / np.linalg.norm(m)
        return len(vecs)

    def score(self, audio):
        """Cosine similarity with the enrolled voiceprint (higher = more likely the user)."""
        if self.enrolled is None or len(audio) < SAMPLE_RATE * 0.5:
            return None
        return float(np.dot(self.embed(audio), self.enrolled))

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({"model": MODEL.name, "vector": self.enrolled.tolist()}))

    def load(self, path):
        data = json.loads(Path(path).read_text())
        self.enrolled = np.array(data["vector"], dtype=np.float32)
        return self


def from_config(cfg):
    """(Voiceprint, threshold) when verification is on and the user has enrolled, else (None, None)."""
    import config
    path = config.voiceprint_file(cfg)
    if path is None:
        return None, None
    return Voiceprint().load(path), float(cfg.get("voice", {}).get("threshold", 0.4))
