"""Fake sherpa_onnx for tests: a speaker "embedding" derived from the audio's level statistics, so the same
synthetic voice (same amplitude pattern) maps to the same vector and different ones differ."""
import numpy as np


class SpeakerEmbeddingExtractorConfig:
    def __init__(self, model, num_threads=1):
        self.model, self.num_threads = model, num_threads


class _Stream:
    def __init__(self):
        self.audio = np.zeros(0, dtype=np.float32)

    def accept_waveform(self, sample_rate, samples):
        self.audio = np.concatenate([self.audio, np.asarray(samples, dtype=np.float32)])

    def input_finished(self):
        pass


class SpeakerEmbeddingExtractor:
    def __init__(self, cfg):
        self.cfg = cfg

    def create_stream(self):
        return _Stream()

    def compute(self, stream):
        a = stream.audio
        if not a.size:
            return [1.0, 0.0, 0.0, 0.0]
        level = float(np.sqrt(np.mean(np.square(a))))
        peak = float(np.max(np.abs(a)))
        sign = float(np.mean(np.sign(a[::7]))) if a.size > 7 else 0.0
        zero_cross = float(np.mean(np.abs(np.diff(np.sign(a))) > 0)) if a.size > 1 else 0.0
        v = np.array([1.0 + level * 10, peak, sign, zero_cross * 5], dtype=np.float32)
        return (v / (np.linalg.norm(v) + 1e-9)).tolist()
