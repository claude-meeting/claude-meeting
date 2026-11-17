# Third-party notices

claude-meeting is licensed under the Apache License 2.0 (see `LICENSE`). Models and most tools are
downloaded from their publishers at install time; the Mac app bundles only `uv`, with its license texts in
`Contents/Resources/licenses`. Each stays under its
own license, listed here. Licenses as published by each project, checked 2026-10-23.

## Bundled in the Mac app

| Component | Use | License |
|---|---|---|
| [uv](https://github.com/astral-sh/uv) | creates the Python environment and installs packages | Apache-2.0 or MIT (at your option) |
| CPython 3.14 (installed by uv at setup, not bundled) | runs the engine | PSF License |

## Applications it works with

| Component | Use | License |
|---|---|---|
| [Minutes](https://github.com/silverstein/minutes) | recording, post-meeting transcript and summary | MIT |
| [Claude Code](https://claude.com/claude-code) | notes and answers, under the user's own account | Anthropic's terms of service |

## Speech and speaker models (downloaded at install time or first use)

| Model | Use | License | Attribution |
|---|---|---|---|
| [TEA-ASR-1.1](https://huggingface.co/JacobLinCool/TEA-ASR-1.1) | speech recognition, Chinese (converted to MLX 8-bit locally) | MIT | JacobLinCool; based on Qwen3-ASR (Apache-2.0) |
| [Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) ([MLX 8-bit](https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-8bit)) | speech recognition, English; Chinese fallback | Apache-2.0 | Qwen team, Alibaba Cloud |
| [Parakeet TDT 0.6B v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) ([MLX](https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v2)) | speech recognition, English fallback | CC-BY-4.0 | NVIDIA |
| [3D-Speaker CAM++ zh/en](https://github.com/modelscope/3D-Speaker) ([ONNX via sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models)) | voiceprints: the user's, and telling other participants apart | Apache-2.0 | Alibaba DAMO Academy, 3D-Speaker |
| [Silero VAD](https://github.com/snakers4/silero-vad) | speech detection | MIT | Silero Team |
| [pyannote speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) | Minutes' post-meeting speaker labels (selected in Minutes' settings) | CC-BY-4.0 | pyannote, Hervé Bredin et al. |

## Python packages (installed by `install.sh` from `app/requirements.txt`)

| Package | License |
|---|---|
| mlx, mlx-audio | MIT |
| numpy | BSD-3-Clause (and bundled permissive licenses) |
| onnxruntime | MIT |
| opencc-python-reimplemented | Apache-2.0 |
| pillow | MIT-CMU |
| sherpa-onnx | Apache-2.0 |
| silero-vad | MIT |
| soundfile | BSD-3-Clause |
| huggingface-hub | Apache-2.0 |
| torch (dependency of silero-vad) | BSD-3-Clause, with bundled Apache-2.0 / MIT parts |
