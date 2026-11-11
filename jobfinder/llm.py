"""Camada fina sobre o Claude: respostas sempre em JSON validado por schema.

Dois backends com a mesma interface:
- "api": API da Anthropic (precisa de ANTHROPIC_API_KEY no .env);
- "cli": Claude Code em modo `claude -p`, usando o login da sua assinatura do Claude.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import anthropic

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


# Helpers para montar JSON Schemas compatíveis com structured outputs
# (todo objeto fecha additionalProperties e exige todas as chaves).
def obj(props: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": list(props) if required is None else required,
        "additionalProperties": False,
    }


def arr(items: dict) -> dict:
    return {"type": "array", "items": items}


STR = {"type": "string"}
INT = {"type": "integer"}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}
ARR_STR = arr(STR)


def enum(*values: str) -> dict:
    return {"type": "string", "enum": list(values)}


def _find_claude_cli() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / ("claude.exe" if sys.platform == "win32" else "claude")
    return str(local) if local.exists() else None


class LLM:
    """Escolhe o backend: API (se houver ANTHROPIC_API_KEY) ou o Claude Code logado na sua assinatura."""

    def __init__(self, model: str):
        self.model = model
        if os.getenv("ANTHROPIC_API_KEY"):
            self.backend = "api"
            self.client = anthropic.Anthropic(max_retries=4)
        else:
            self.backend = "cli"
            self.cli = _find_claude_cli()
            if not self.cli:
                raise LLMError(
                    "Sem ANTHROPIC_API_KEY no .env e o Claude Code (comando 'claude') não foi encontrado. "
                    "Instale com:  irm https://claude.ai/install.ps1 | iex   e faça login com:  claude auth login"
                )

    def json(self, **kwargs) -> dict:
        if self.backend == "cli":
            # Vários `claude -p` em paralelo podem colidir ao renovar o login: espera e tenta de novo.
            for attempt in range(3):
                try:
                    return self._json_cli(**kwargs)
                except LLMError as e:
                    transient = any(s in str(e) for s in ("OAuth", "refresh", "overloaded", "rate limit", "529"))
                    if not transient or attempt == 2:
                        raise
                    time.sleep(20 * (attempt + 1))
        return self._json_api(**kwargs)

    def _json_cli(
        self,
        *,
        system: str,
        content,
        schema: dict,
        effort: str = "medium",
        max_tokens: int = 16000,
        tools: list | None = None,
    ) -> dict:
        """Mesma interface, executada via `claude -p` (usa o login da sua assinatura do Claude)."""
        if not isinstance(content, str):
            content = "\n\n".join(b["text"] for b in content if b.get("type") == "text")
        # Busca na web da API -> ferramentas WebSearch/WebFetch do Claude Code.
        cli_tools = "WebSearch,WebFetch" if any(t.get("name") == "web_search" for t in tools or []) else ""
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(system)
            system_file = f.name
        cmd = [
            self.cli, "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema, ensure_ascii=False),
            "--system-prompt-file", system_file,
            "--model", self.model,
            "--effort", effort,
            "--tools", cli_tools,
            "--no-session-persistence",
        ]
        if cli_tools:
            cmd += ["--allowedTools", cli_tools]
        try:
            proc = subprocess.run(
                cmd, input=content, capture_output=True, text=True, encoding="utf-8",
                timeout=900, cwd=tempfile.gettempdir(),
            )
        except subprocess.TimeoutExpired as e:
            raise LLMError("O Claude Code demorou demais para responder.") from e
        finally:
            os.unlink(system_file)
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise LLMError(f"Claude Code falhou: {(proc.stderr or proc.stdout).strip()[:300]}") from e
        if out.get("is_error"):
            msg = str(out.get("result") or out.get("terminal_reason") or "erro desconhecido")
            if "login" in msg.lower():
                msg += "  (rode:  claude auth login)"
            raise LLMError(f"Claude Code: {msg}")
        if isinstance(out.get("structured_output"), dict):
            return out["structured_output"]
        try:
            return json.loads(out.get("result") or "")
        except json.JSONDecodeError as e:
            raise LLMError("Claude Code não devolveu JSON válido.") from e

    def _json_api(
        self,
        *,
        system: str,
        content,
        schema: dict,
        effort: str = "medium",
        max_tokens: int = 16000,
        tools: list | None = None,
    ) -> dict:
        """Uma chamada ao Claude cuja resposta final é JSON no formato `schema`.

        `content` pode ser string ou lista de blocos (ex.: PDF + texto).
        Com server tools (web_search), continua a conversa em caso de pause_turn.
        """
        messages = [{"role": "user", "content": content}]
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            # Prefixo estável (system) fica em cache entre chamadas -> mais barato.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        # Se o classificador de segurança recusar, a API refaz em outro modelo (não existe no Haiku).
        if "haiku" not in self.model:
            kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
        if tools:
            kwargs["tools"] = tools

        for _ in range(6):
            try:
                with self.client.beta.messages.stream(messages=messages, **kwargs) as stream:
                    resp = stream.get_final_message()
            except anthropic.AuthenticationError as e:
                raise LLMError("ANTHROPIC_API_KEY inválida ou ausente (confira o arquivo .env).") from e
            except anthropic.BadRequestError as e:
                raise LLMError(f"Requisição rejeitada pela API: {e.message}") from e
            except anthropic.RateLimitError as e:
                raise LLMError("Limite de uso da API atingido; tente novamente mais tarde.") from e
            except anthropic.APIStatusError as e:
                raise LLMError(f"Erro da API ({e.status_code}): {e.message}") from e
            except anthropic.APIConnectionError as e:
                raise LLMError("Falha de conexão com a API do Claude.") from e

            if resp.stop_reason == "pause_turn":
                messages = messages + [{"role": "assistant", "content": resp.content}]
                continue
            if resp.stop_reason == "refusal":
                raise LLMError("O modelo recusou esta solicitação.")
            if resp.stop_reason == "max_tokens":
                raise LLMError("Resposta cortada por max_tokens.")
            return _parse_final_json(resp.content)
        raise LLMError("A busca não terminou após várias continuações.")


def _parse_final_json(content) -> dict:
    texts = [b.text for b in content if b.type == "text"]
    if not texts:
        raise LLMError("Resposta sem texto.")
    # Com server tools podem existir textos antes das buscas; o JSON é o final.
    for candidate in (texts[-1], "".join(texts)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    raise LLMError("Resposta não veio em JSON válido.")
