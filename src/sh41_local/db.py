from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .paths import private_dir
from .spec import AgentSpec

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (slug TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS agents (
 id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, harness TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS deployments (
 id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id),
 workspace TEXT NOT NULL REFERENCES workspaces(slug), spec TEXT NOT NULL,
 fingerprint TEXT NOT NULL, status TEXT NOT NULL, container TEXT,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 ended_at TEXT, error TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_deployment
 ON deployments(agent_id) WHERE ended_at IS NULL;
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id), native_id TEXT,
 active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_session ON sessions(agent_id) WHERE active=1;
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id),
 session_id TEXT NOT NULL REFERENCES sessions(id), status TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 ended_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(agent_id)
 WHERE status IN ('pending','running');
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
 sequence INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(run_id,sequence)
);
CREATE TABLE IF NOT EXISTS operations (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, resource TEXT NOT NULL,
 status TEXT NOT NULL, progress TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_operation ON operations(resource)
 WHERE status IN ('pending','running');
PRAGMA user_version=2;
"""


class Store:
    def __init__(self, root: Path):
        self.root = private_dir(root)
        self.path = root / "state.sqlite3"
        if self.path.is_symlink():
            raise ValueError("Database cannot be a symlink")
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise ValueError("Database was written by a newer sh41 version")
            conn.executescript("BEGIN IMMEDIATE;" + SCHEMA + "COMMIT;")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def agents(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("""
                SELECT a.*, d.workspace, COALESCE(d.status,'parked') AS state
                FROM agents a LEFT JOIN deployments d ON d.agent_id=a.id AND d.ended_at IS NULL
                ORDER BY a.slug
            """)]

    def operations(self, limit=50) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM operations ORDER BY rowid DESC LIMIT ?", (limit,))]

    def operation(self, ident):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM operations WHERE id=?", (ident,)).fetchone()
            return dict(row) if row else None

    def add_operation(self, ident, kind, resource):
        try:
            with self.connect() as conn:
                conn.execute("INSERT INTO operations(id,kind,resource,status) VALUES (?,?,?,'pending')",
                             (ident, kind, resource))
        except sqlite3.IntegrityError:
            raise ValueError("An operation is already active for this resource") from None

    def update_operation(self, ident, status, progress=""):
        with self.connect() as conn:
            conn.execute("""UPDATE operations SET status=?,progress=?,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                (status, progress, ident))

    def interrupt_operations(self):
        with self.connect() as conn:
            conn.execute("""UPDATE operations SET status='interrupted',
                progress='Supervisor restarted; inspect state before retrying',
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE status IN ('pending','running')""")

    def agent(self, slug: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM agents WHERE slug=?", (slug,)).fetchone()
            if row is None:
                raise ValueError(f"Unknown agent: {slug}")
            return dict(row)

    def deployment(self, slug: str, *, latest=False) -> dict:
        agent = self.agent(slug)
        with self.connect() as conn:
            clause = "" if latest else "AND ended_at IS NULL"
            row = conn.execute(
                f"SELECT * FROM deployments WHERE agent_id=? {clause} ORDER BY rowid DESC LIMIT 1",
                (agent["id"],),
            ).fetchone()
            if row is None:
                raise ValueError("Agent is parked; run sh41 redeploy first")
            return dict(row)

    def reserve(self, spec: AgentSpec) -> tuple[dict, bool]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO workspaces VALUES (?)", (spec.workspace,))
            conn.execute("INSERT OR IGNORE INTO agents(id,slug,harness) VALUES (?,?,?)",
                         (str(uuid.uuid4()), spec.agent, spec.harness))
            agent = conn.execute("SELECT * FROM agents WHERE slug=?", (spec.agent,)).fetchone()
            if agent["harness"] != spec.harness:
                raise ValueError("An identity's harness is immutable; choose a new agent name")
            existing = conn.execute(
                "SELECT * FROM deployments WHERE agent_id=? AND ended_at IS NULL", (agent["id"],)
            ).fetchone()
            if existing:
                if existing["fingerprint"] != spec.fingerprint():
                    raise ValueError("Agent already deployed with another spec; park it first")
                return dict(existing), False
            ident = str(uuid.uuid4())
            conn.execute("""INSERT INTO deployments
                (id,agent_id,workspace,spec,fingerprint,status) VALUES (?,?,?,?,?,'provisioning')""",
                (ident, agent["id"], spec.workspace, spec.as_yaml(), spec.fingerprint()))
            return dict(conn.execute("SELECT * FROM deployments WHERE id=?", (ident,)).fetchone()), True

    def set_deployment(self, ident: str, status: str, *, container=None, end=False, error=None):
        with self.connect() as conn:
            conn.execute("""UPDATE deployments SET status=?,container=COALESCE(?,container),
                ended_at=CASE WHEN ? THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE ended_at END,
                error=? WHERE id=?""", (status, container, end, error, ident))

    def session(self, slug: str, *, new=False, resume: str | None = None) -> dict:
        agent = self.agent(slug)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if new or resume:
                busy = conn.execute("SELECT 1 FROM runs WHERE agent_id=? AND status IN ('pending','running')",
                                    (agent["id"],)).fetchone()
                if busy:
                    raise ValueError("Cannot change session during a run")
                if resume and not conn.execute("SELECT 1 FROM sessions WHERE id=? AND agent_id=?",
                                               (resume, agent["id"])).fetchone():
                    raise ValueError("Session does not belong to this agent")
                conn.execute("UPDATE sessions SET active=0 WHERE agent_id=?", (agent["id"],))
                if resume:
                    conn.execute("UPDATE sessions SET active=1 WHERE id=?", (resume,))
            row = conn.execute("SELECT * FROM sessions WHERE agent_id=? AND active=1",
                               (agent["id"],)).fetchone()
            if not row:
                ident = str(uuid.uuid4())
                conn.execute("INSERT INTO sessions(id,agent_id) VALUES (?,?)", (ident, agent["id"]))
                row = conn.execute("SELECT * FROM sessions WHERE id=?", (ident,)).fetchone()
            return dict(row)

    def sessions(self, slug: str) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM sessions WHERE agent_id=? ORDER BY rowid",
                                                (self.agent(slug)["id"],))]

    def start_run(self, slug: str) -> dict:
        session = self.session(slug)
        ident = str(uuid.uuid4())
        try:
            with self.connect() as conn:
                conn.execute("INSERT INTO runs(id,agent_id,session_id,status) VALUES (?,?,?,'pending')",
                             (ident, session["agent_id"], session["id"]))
        except sqlite3.IntegrityError:
            raise ValueError("Agent already has an active run") from None
        return {"id": ident, "session_id": session["id"], "native_id": session["native_id"]}

    def finish_run(self, ident: str, status: str, native_id: str | None = None):
        with self.connect() as conn:
            conn.execute("UPDATE runs SET status=?,ended_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                         (status, ident))
            if native_id:
                conn.execute("UPDATE sessions SET native_id=? WHERE id=(SELECT session_id FROM runs WHERE id=?)",
                             (native_id, ident))

    def event(self, ident: str, sequence: int, payload: dict):
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO events(run_id,sequence,payload) VALUES (?,?,?)",
                         (ident, sequence, json.dumps(payload)))

    def history(self, slug: str) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM runs WHERE agent_id=? ORDER BY rowid",
                                                (self.agent(slug)["id"],))]

    def native_session(self, session_id, native_id):
        if native_id:
            with self.connect() as conn:
                conn.execute("UPDATE sessions SET native_id=? WHERE id=?", (native_id, session_id))

    def run(self, slug, ident):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=? AND agent_id=?",
                               (ident, self.agent(slug)["id"])).fetchone()
            if row is None:
                raise ValueError("Run does not belong to this agent")
            return dict(row)

    def events(self, ident, offset=0):
        with self.connect() as conn:
            return [json.loads(row[0]) for row in conn.execute(
                "SELECT payload FROM events WHERE run_id=? AND sequence>=? ORDER BY sequence",
                (ident, offset))]
