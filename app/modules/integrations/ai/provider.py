"""Proveedor de IA. La interfaz es mínima para poder probar el agente sin red; la implementación real usa el SDK oficial."""
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import get_settings


class AIUnavailable(Exception):
    """El proveedor no respondió (clave inválida, saturación, red). El agente escala a una persona."""


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class AIResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_content: Any = None         # tal cual lo devolvió el modelo: se reenvía SIN tocar al continuar el ciclo de herramientas
    refusal: bool = False                 # el modelo (o sus clasificadores de seguridad) rechazó la solicitud
    input_tokens: int = 0
    output_tokens: int = 0


class AIProvider(Protocol):
    async def complete(self, *, system: list[dict[str, Any]], messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AIResult: ...


class AnthropicProvider:
    """Claude vía el SDK oficial `anthropic`. Sin `thinking`/`temperature`: en Claude Opus 5.5 el razonamiento es
    adaptativo y la profundidad se controla con `output_config.effort`."""

    def __init__(self, api_key: str, *, model: str | None = None, client: Any = None) -> None:
        s = get_settings()
        self.model, self.effort, self.max_tokens = model or s.ai_model, s.ai_effort, s.ai_max_tokens
        self.fallback, self.fallback_model = s.ai_refusal_fallback, s.ai_fallback_model
        if client is None:
            import anthropic  # carga perezosa: el SDK es pesado y solo hace falta cuando de verdad se llama a la IA
            client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=45.0)
        self.client = client

    async def complete(self, *, system: list[dict[str, Any]], messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AIResult:
        kw: dict[str, Any] = dict(model=self.model, max_tokens=self.max_tokens, system=system, messages=messages,
                                  output_config={"effort": self.effort})
        if tools:
            kw["tools"] = tools
        import anthropic
        try:
            if self.fallback:   # si un clasificador de seguridad rechaza, la API reintenta sola en otro modelo
                r = await self.client.beta.messages.create(betas=["server-side-fallback-2026-06-01"],
                                                           fallbacks=[{"model": self.fallback_model}], **kw)
            else:
                r = await self.client.messages.create(**kw)
        except anthropic.AuthenticationError as e:
            raise AIUnavailable("La clave de IA no es válida") from e
        except anthropic.RateLimitError as e:
            raise AIUnavailable("El proveedor de IA está saturado") from e
        except anthropic.APIConnectionError as e:
            raise AIUnavailable("No se pudo contactar al proveedor de IA") from e
        except anthropic.APIStatusError as e:
            raise AIUnavailable(f"Error del proveedor de IA ({e.status_code})") from e
        if r.stop_reason == "refusal":
            return AIResult(refusal=True, assistant_content=r.content)
        return AIResult(
            text="".join(b.text for b in r.content if b.type == "text").strip(),
            tool_calls=[ToolCall(b.id, b.name, dict(b.input)) for b in r.content if b.type == "tool_use"],
            assistant_content=r.content, input_tokens=r.usage.input_tokens, output_tokens=r.usage.output_tokens)

    async def ping(self) -> None:
        """Validación de credenciales: una llamada mínima. Lanza AIUnavailable si algo falla."""
        await self.complete(system=[{"type": "text", "text": "Responde solo: ok"}],
                            messages=[{"role": "user", "content": "ping"}], tools=[])


def provider_for(api_key: str | None) -> AnthropicProvider:
    key = api_key or get_settings().anthropic_api_key
    if not key:
        raise AIUnavailable("No hay clave de IA configurada")
    return AnthropicProvider(key)
