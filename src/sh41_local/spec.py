from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

Slug = Annotated[str, Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")]
EnvName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SecretRef(StrictModel):
    env: EnvName


def endpoint(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Expected an HTTP(S) endpoint")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Use secret references, not credentials or query parameters in URLs")
    return value.rstrip("/")


class Inference(StrictModel):
    provider: Literal["native", "ollama", "openai-compatible"] = "native"
    base_url: str | None = None
    api: Literal["chat-completions"] = "chat-completions"
    api_key: SecretRef | None = None

    @field_validator("base_url")
    @classmethod
    def valid_endpoint(cls, value):
        return endpoint(value) if value is not None else None

    @model_validator(mode="after")
    def valid_provider(self):
        if self.provider == "openai-compatible" and not self.base_url:
            raise ValueError("Compatible inference requires base_url")
        if self.provider != "openai-compatible" and self.base_url:
            raise ValueError("base_url is only supported for compatible inference")
        return self


class Sandbox(StrictModel):
    provider: Literal["docker"] = "docker"


class Source(StrictModel):
    provider: Literal["local"] = "local"
    path: str


class MCP(StrictModel):
    transport: Literal["stdio", "http"]
    command: list[str] | None = None
    url: str | None = None
    env: dict[EnvName, SecretRef] = Field(default_factory=dict)
    headers: dict[str, SecretRef] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def valid_endpoint(cls, value):
        return endpoint(value) if value is not None else None

    @model_validator(mode="after")
    def valid_transport(self):
        if self.transport == "stdio":
            if not self.command or any(not s or "\0" in s for s in self.command):
                raise ValueError("stdio MCP requires a nonempty command array")
            if self.url or self.headers:
                raise ValueError("stdio MCP does not accept url or headers")
        elif not self.url or self.command or self.env:
            raise ValueError("HTTP MCP requires url and accepts headers, not command/env")
        return self


class AgentSpec(StrictModel):
    version: Literal[1] = 1
    agent: Slug
    harness: Literal["opencode", "claude-code", "codex"] = "opencode"
    workspace: Slug = "default"
    sandbox: Sandbox = Field(default_factory=Sandbox)
    source: Source | None = None
    model: str | None = None
    inference: Inference = Field(default_factory=Inference)
    instructions: str | None = None
    mcp: dict[Slug, MCP] = Field(default_factory=dict)

    @field_validator("model")
    @classmethod
    def valid_model(cls, value):
        if value is not None and (not value.strip() or re.search(r"[\x00-\x1f]", value)):
            raise ValueError("Model must be a nonempty identifier")
        return value

    @model_validator(mode="after")
    def supported_combination(self):
        if self.harness == "opencode":
            if not self.model or self.inference.provider == "native":
                raise ValueError("OpenCode requires model and Ollama or a compatible endpoint")
        elif self.inference.provider != "native":
            raise ValueError("Claude Code and Codex use native inference in this MVP")
        return self

    def resolved(self, base: Path, *, check: bool = True) -> AgentSpec:
        result = self.model_copy(deep=True)
        if result.source:
            path = (base / Path(result.source.path).expanduser()).resolve()
            if check and not path.is_dir():
                raise ValueError("Source must be an existing local directory")
            result.source.path = str(path)
        if result.instructions:
            path = (base / Path(result.instructions).expanduser()).resolve()
            if check and not path.is_file():
                raise ValueError("Instructions must be an existing file")
            result.instructions = str(path)
        return result

    def as_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(exclude_none=True), sort_keys=False)

    def fingerprint(self) -> str:
        raw = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def secret_names(self) -> set[str]:
        refs = [self.inference.api_key] if self.inference.api_key else []
        for server in self.mcp.values():
            refs.extend(server.env.values())
            refs.extend(server.headers.values())
        return {ref.env for ref in refs}


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError("YAML keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def parse_yaml(text: str) -> AgentSpec:
    if len(text.encode()) > 1024 * 1024:
        raise ValueError("Manifest exceeds 1 MiB")
    try:
        return AgentSpec.model_validate(yaml.load(text, Loader=UniqueLoader))
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
            for item in exc.errors(include_input=False, include_url=False)
        )
        raise ValueError(detail) from None
    except yaml.YAMLError:
        raise ValueError("Invalid YAML syntax") from None
