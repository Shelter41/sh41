"""Wizard choices, checked against native harness docs on 2026-09-06.

https://code.claude.com/docs/en/model-config
https://platform.claude.com/docs/en/models/overview
https://platform.claude.com/docs/en/about-claude/model-deprecations
https://learn.chatgpt.com/docs/models
These are documented choices, not an account entitlement probe.
"""

NATIVE_MODELS = {
    "claude-code": (
        ("Default (account)", ""),
        ("Best available (Fable or Opus)", "best"),
        ("Fable (latest)", "fable"),
        ("Opus (latest)", "opus"),
        ("Sonnet (latest)", "sonnet"),
        ("Haiku (latest)", "haiku"),
        ("Fable 5.1", "claude-fable-5-1"),
        ("Fable 5", "claude-fable-5"),
        ("Opus 5", "claude-opus-5"),
        ("Opus 4.8", "claude-opus-4-8"),
        ("Opus 4.7", "claude-opus-4-7"),
        ("Opus 4.6", "claude-opus-4-6"),
        ("Opus 4.5", "claude-opus-4-5-20251101"),
        ("Sonnet 5", "claude-sonnet-5"),
        ("Sonnet 4.6", "claude-sonnet-4-6"),
        ("Sonnet 4.5", "claude-sonnet-4-5-20250929"),
        ("Haiku 4.5", "claude-haiku-4-5-20251001"),
        ("Fable (1M context)", "fable[1m]"),
        ("Opus (1M context)", "opus[1m]"),
        ("Sonnet (1M context)", "sonnet[1m]"),
        ("Opus 4.8 (1M context)", "claude-opus-4-8[1m]"),
        ("Opus 4.7 (1M context)", "claude-opus-4-7[1m]"),
        ("Opus 4.6 (1M context)", "claude-opus-4-6[1m]"),
        ("Sonnet 4.6 (1M context)", "claude-sonnet-4-6[1m]"),
        ("Opus plan / Sonnet execution", "opusplan"),
    ),
    "codex": (
        ("Default (account)", ""),
        ("GPT-6 Astra", "gpt-6-astra"),
        ("GPT-5.6 Sol", "gpt-5.6-sol"),
        ("GPT-5.6 Terra", "gpt-5.6-terra"),
        ("GPT-5.6 Luna", "gpt-5.6-luna"),
        ("GPT-5.3 Codex Spark (Pro preview)", "gpt-5.3-codex-spark"),
        ("GPT-5.5", "gpt-5.5"),
    ),
}
