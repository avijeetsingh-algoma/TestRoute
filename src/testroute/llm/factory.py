"""Builds the providers a condition needs.

Component: C5 (LLM Client), provider wiring.

Responsibility:
    Creates the provider for each role a condition uses (spec C4 and C5):
    * ``local`` -> :class:`OllamaProvider` with the model and options of
      config/models.yaml, after a health check of ``/api/version`` and a
      check that the model is pulled (spec Section 7: health check before a
      batch);
    * ``cloud`` -> :class:`DeepSeekProvider` with the key and base URL from
      .env;
    * either role -> :class:`ReplayProvider` when replay is requested for it.
    A role that cannot be served raises :class:`ProviderUnavailable` with an
    instruction, so the caller can stop before any call is made instead of
    silently using the other model.

Receives: a Condition, the models config, env settings, replay options.
Produces: ``dict[role, LLMProvider]``.
Talks to: ``llm.ollama``, ``llm.deepseek``, ``llm.replay``.
Spec sections: 5 (C4, C5), 7, 12 (run.bat Ollama check).
Feedback IDs: FB12 (fixed configured models).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from testroute.llm.base import InfraError, LLMProvider
from testroute.llm.deepseek import DeepSeekProvider
from testroute.llm.ollama import OllamaProvider
from testroute.llm.replay import ReplayProvider
from testroute.router import CLOUD, LOCAL, Condition
from testroute.settings import EnvSettings


class ProviderUnavailable(Exception):
    """A provider the condition needs cannot be used; the message says why and what to do.

    Attributes:
        role: ``local`` or ``cloud``.
    """

    def __init__(self, role: str, message: str) -> None:
        super().__init__(message)
        self.role = role


def build_ollama(models: dict[str, Any], env: EnvSettings) -> OllamaProvider:
    """Ollama provider from the ``local`` and ``sampling`` blocks of models.yaml."""
    local, sampling = models["local"], models["sampling"]
    return OllamaProvider(
        env.ollama_host,
        str(local["model"]),
        temperature=float(sampling["temperature"]),
        max_output_tokens=int(sampling["max_output_tokens"]),
        num_ctx=int(local["num_ctx"]),
        keep_alive=str(local["keep_alive"]),
        seed_per_call=bool(local.get("seed_per_call", True)),
        timeout_s=float(local["request_timeout_s"]),
        retry=models.get("retries"),
    )


def build_deepseek(models: dict[str, Any], env: EnvSettings) -> DeepSeekProvider:
    """DeepSeek provider from the ``cloud`` and ``sampling`` blocks and .env."""
    cloud, sampling = models["cloud"], models["sampling"]
    if str(cloud.get("thinking", "disabled")) != "disabled":
        # Spec Section 2 is locked: the provider always sends thinking disabled.
        raise ProviderUnavailable(CLOUD, "config/models.yaml cloud.thinking must be 'disabled' (spec Section 2)")
    return DeepSeekProvider(
        env.deepseek_api_key,
        env.deepseek_base_url,
        str(cloud["model"]),
        temperature=float(sampling["temperature"]),
        max_output_tokens=int(sampling["max_output_tokens"]),
        timeout_s=float(cloud["request_timeout_s"]),
        retry=models.get("retries"),
    )


def build_providers(
    condition: Condition,
    models: dict[str, Any],
    env: EnvSettings,
    *,
    replay_dir: Path | None = None,
    replay_roles: frozenset[str] = frozenset(),
) -> dict[str, LLMProvider]:
    """Create one provider per role the condition uses.

    Args:
        condition: The run's condition.
        models: Loaded config/models.yaml.
        env: Loaded .env settings.
        replay_dir: Results folder to replay from (needed if replay_roles).
        replay_roles: Roles to serve from recordings instead of live.

    Returns:
        Role -> provider.

    Raises:
        ProviderUnavailable: when Ollama is unreachable or the model is not
            pulled, the key is missing, or replay data is missing.

    Side effects:
        Probes the Ollama server for local roles.
    """
    roles = [r for r, used in ((LOCAL, condition.uses_local), (CLOUD, condition.uses_cloud)) if used]
    providers: dict[str, LLMProvider] = {}
    replay: ReplayProvider | None = None
    for role in roles:
        if role in replay_roles:
            if replay_dir is None or not (replay_dir / "calls.jsonl").is_file():
                raise ProviderUnavailable(role, f"no recorded calls to replay in {replay_dir}")
            replay = replay or ReplayProvider(replay_dir)
            providers[role] = replay
        elif role == LOCAL:
            provider = build_ollama(models, env)
            try:
                provider.health_check()
                provider.digest()
            except InfraError as exc:
                raise ProviderUnavailable(LOCAL, f"{exc}. Start Ollama, or choose a cloud condition such as C-F4.") from None
            providers[role] = provider
        else:
            if not env.api_key_set:
                raise ProviderUnavailable(CLOUD, "DEEPSEEK_API_KEY is empty. Open .env and paste the key after DEEPSEEK_API_KEY=")
            providers[role] = build_deepseek(models, env)
    return providers
