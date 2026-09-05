# Decisions

- Keep an independent package and repository so local execution cannot accidentally depend on the SaaS installation.
- Use Apache-2.0 for sh41 code while leaving harness and model licensing independent.
- Keep `sh41` as the executable and `sh41-local` as the distribution to preserve familiarity without conflating packages.
- Use SQLite and a user-local Unix socket supervisor to coordinate concurrent CLI processes without infrastructure services.
- Use Docker as the sole MVP sandbox to provide one reproducible execution boundary on macOS and Linux.
- Copy sources into per-identity workspaces so agent edits cannot silently overwrite the user's project.
- Retain cloud Claude/Codex driver conventions but remove cloud snapshots because native state lives on persistent local storage.
- Use OpenCode for compatible/open-weight inference instead of claiming every harness supports every provider.
- Run Ollama on the host to retain native hardware acceleration and share its model cache across agents.
- Require explicit credential import or environment references because host credentials should not be silently mounted into agents.
- Preserve direct TUI history natively rather than inventing unreliable run boundaries from terminal scraping.
- Do not replay uncertain or interrupted turns because tool actions may already have happened.
- Keep network egress unrestricted for the MVP and document Docker's limits rather than advertising hostile-code or air-gap security.
