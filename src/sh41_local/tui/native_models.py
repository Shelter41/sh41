"""Wizard choices, checked against native harness docs on 2026-09-06.

https://code.claude.com/docs/en/model-config
https://learn.chatgpt.com/docs/models
These are documented choices, not an account entitlement probe.
"""

NATIVE_MODELS = {
    "claude-code": (
        ("Default (account)", ""),
        ("Opus", "opus"),
        ("Sonnet", "sonnet"),
        ("Haiku", "haiku"),
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
