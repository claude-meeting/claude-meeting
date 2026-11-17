"""Fake `mlx_audio` for tests: load_model() returns a model whose generate() answers from the audio's
level instead of its content, so the engine's guards and fallback logic can be exercised without MLX."""
