"""Client LLM minimal (format « chat completions ») derrière une interface remplaçable.

Les agents ne dépendent que de `LLMClient.complete(system, user)` : changer de modèle ou
de fournisseur ne touche pas aux agents. Deux fournisseurs, même format d'API :
- `mistral` : API Mistral (éditeur européen), clé MISTRAL_API_KEY ;
- `ollama`  : modèle open-weight exécuté en local (ex. qwen2.5:7b) : aucune donnée ne sort
              de la machine (option « hébergé en interne » de l'étude).
BIFLOW_LLM_PROVIDER = auto (défaut : mistral si une clé existe, sinon aucun) | mistral |
ollama | none. Sans LLM, `get_llm()` retourne None et les agents utilisent la baseline.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

import httpx

from biflow.common.config import get_settings

log = logging.getLogger(__name__)


class LLMClient(Protocol):
    model: str

    def complete(self, system: str, user: str) -> str: ...


class ChatClient:
    """Client d'une API au format chat completions (Mistral, Ollama /v1)."""

    def __init__(self, model: str, base_url: str, api_key: str | None = None,
                 client: httpx.Client | None = None, attempts: int = 3, timeout: float = 60,
                 provider: str = "mistral") -> None:
        self.model = model
        self.provider = provider
        self.attempts = attempts
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = client or httpx.Client(base_url=base_url, timeout=timeout, headers=headers)

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        for attempt in range(1, self.attempts + 1):
            resp = self.client.post("/chat/completions", json=payload)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.attempts:
                log.warning("LLM HTTP %s, nouvel essai %d", resp.status_code, attempt + 1)
                time.sleep(2 * attempt)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        raise RuntimeError("unreachable")


def get_llm() -> LLMClient | None:
    s = get_settings()
    provider = s.llm_provider
    if provider == "auto":
        provider = "mistral" if s.mistral_api_key else "none"
    if provider == "mistral":
        if not s.mistral_api_key:
            log.warning("BIFLOW_LLM_PROVIDER=mistral sans MISTRAL_API_KEY : baseline sans LLM")
            return None
        return ChatClient(s.mistral_model, s.mistral_base_url, s.mistral_api_key, provider="mistral")
    if provider == "ollama":
        # modèle local : la première réponse peut être lente (chargement en mémoire)
        return ChatClient(s.ollama_model, s.ollama_base_url, timeout=300, provider="ollama")
    return None


def llm_label(llm: LLMClient | None) -> str | None:
    """« fournisseur:modèle » pour les journaux et le dashboard."""
    if llm is None:
        return None
    return getattr(llm, "label", llm.model)
