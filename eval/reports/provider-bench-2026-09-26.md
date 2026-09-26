# Provider bench

Time to first token (TTFT) and decode speed per model, measured with `careflow providers bench` on the owner's MacBook Pro M3 Pro (18 GB). Tokens are approximated as characters / 4. Synthetic prompt only.

| Served model | Config model | Task | Runs | TTFT p50 (s) | TTFT max (s) | Tokens/s p50 |
|---|---|---|---|---|---|---|
| mlx-community/Qwen3.5-9B-MLX-4bit@938d891 | qwen3.5-9b-4bit | extraction | 5 | 0.25 | 1.38 | 27.5 |
| mlx-community/Qwen3.5-4B-4bit@0e7ffd5 | qwen3.5-9b-4bit | extraction | 5 | 0.22 | 0.55 | 58.0 |
