# Coding Directory Selection

Implemented in the standalone project. No SaaS dependencies or changes.

## Phase 1: Inspection and Bindings

Read-only Git-aware source inspection, repository/folder associations, explicit
source.mode, and SQLite v3 immutable directory bindings. Legacy YAML defaults to
copy; existing private working directories are backfilled in place.

Acceptance: ordinary/nested/linked repositories, aliases, invalid paths, dirty
state, migration idempotence, and legacy deployment fingerprint compatibility.

## Phase 2: Working Directories

Repositories use committed-HEAD worktrees with separate branches and shared Git
metadata. Non-Git folders use explicit direct/copy access without Git initialization.
Container mounts preserve working Git operations on macOS; private state is never
exposed by a selected folder or replacement symlink. Host setup disables hooks and
rejects checkout filters/submodules. Bare/unborn repositories fail clearly.

Acceptance: real Docker edits/commits in two worktrees, unchanged original files,
two acknowledged direct agents sharing host files, private-copy exclusions, branch
collisions, and unchanged host repository configuration and folder permissions.

## Phase 3: Wizard and CLI

Directory autocomplete is retained. Asynchronous inspection presents the worktree
path, branch, committed HEAD and dirty-state warning, or an explicit original/copy
choice. Associated agents show recorded lifecycle and access mode. CLI adds
--source-mode and --allow-shared-folder; generated YAML records the chosen mode.

Acceptance: 80x24 and 120x40 wizard flows, keyboard sharing confirmation, errors and
stale results, back navigation, YAML round trips, and side-effect-free Save Only.

## Phase 4: Sharing and Recovery

Transactional reservation rechecks shared-folder acknowledgements against all
overlapping direct bindings. Per-repository locks serialize creation. Durable
preparation states recover unused or fully checked-out clean worktrees; ambiguous
files remain untouched with a repair-required error. Source/mode are immutable.
Pause/park/redeploy retain bindings, files and branches; direct export is unnecessary.

Acceptance: concurrent direct launches and worktree creation, stale acknowledgements,
parked/parent-child/alias associations, interrupted setup, recreation persistence,
missing/moved paths and preservation of agent edits.

## Phase 5: Acceptance Record

On macOS with Docker Desktop: 89 non-live tests passed, including real Docker,
terminal and separately installed-wheel acceptance. The 13 provider-dependent
live tests were excluded; this change does not require provider usage or a reboot.
Scoped Ruff (src/tests), source/wheel builds and compact-terminal screenshots passed.
README, architecture, decisions, examples and test instructions reflect these modes.

Detection covers one SH41_LOCAL_HOME. No automatic merge, branch deletion,
worktree cleanup or cloud integration is included. Shared Git metadata is not a
repository security boundary; direct-folder access includes hidden host files.
