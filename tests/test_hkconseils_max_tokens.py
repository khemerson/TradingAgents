"""Fork HKCONSEILS - garde-fou sur le plafond de sortie des LLM (invariant 1).

Le plafond de 16000 est une valeur calibree (audit 14 jours, cf. HKCONSEILS_CONFIG.md).
Ces tests echouent si un futur merge amont, ou une modification locale, le retire, le
change silencieusement, ou casse la priorite de l'appelant.

Le test decisif est `test_le_plafond_part_bien_dans_la_requete` : il monte un serveur
d'echo local et lit le corps JSON reellement emis. Verifier seulement l'attribut du
client ne suffit pas - langchain-openai serialise `max_tokens` sous le nom
`max_completion_tokens`, et un changement de nom cote SDK passerait inapercu.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tradingagents.llm_clients.openai_client import (
    _HK_DEFAULT_MAX_TOKENS,
    _PASSTHROUGH_KWARGS,
    OpenAIClient,
)

BASE_URL = "http://127.0.0.1:4000/v1"

# Noms acceptes pour le plafond sur le fil. `max_completion_tokens` est la forme
# moderne emise par langchain-openai >= 1.x ; `max_tokens` est la forme historique.
_WIRE_KEYS = ("max_completion_tokens", "max_tokens")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Neutralise la surcharge d'environnement sauf quand un test la pose lui-meme."""
    monkeypatch.delenv("HKCONSEILS_MAX_TOKENS", raising=False)
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "placeholder-not-a-secret")


def _build(model="qwen38-27b", provider="openai_compatible", base_url=BASE_URL, **kwargs):
    return OpenAIClient(model, base_url, provider=provider, **kwargs).get_llm()


# --------------------------------------------------------------------------
# Serveur d'echo : capture le corps reellement envoye
# --------------------------------------------------------------------------

class _EchoHandler(BaseHTTPRequestHandler):
    captured: dict = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        type(self).captured["body"] = json.loads(self.rfile.read(length) or b"{}")
        payload = json.dumps({
            "id": "echo", "object": "chat.completion", "created": 0, "model": "echo",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def echo_server():
    _EchoHandler.captured = {}
    srv = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1", _EchoHandler.captured
    srv.shutdown()


def _wire_cap(body):
    """Valeur du plafond dans le corps emis, quel que soit le nom retenu par le SDK."""
    for key in _WIRE_KEYS:
        if key in body:
            return body[key]
    return None


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_valeur_calibree_inchangee():
    """Le plafond par defaut vaut 16000 - pas 40000, pas une valeur amont."""
    assert _HK_DEFAULT_MAX_TOKENS == 16000


def test_max_tokens_est_forwarde_depuis_la_config():
    """Sans cette entree, une valeur de config serait filtree avant d'atteindre le client."""
    assert "max_tokens" in _PASSTHROUGH_KWARGS


def test_le_plafond_part_bien_dans_la_requete(echo_server):
    """Test decisif : le plafond est present dans le corps HTTP reellement emis."""
    base_url, captured = echo_server
    _build(base_url=base_url).invoke("ping")
    body = captured["body"]
    assert _wire_cap(body) == 16000, (
        f"plafond absent ou different dans le corps emis : {body!r}. "
        f"Si le SDK a renomme le champ, ajouter le nouveau nom a _WIRE_KEYS."
    )


def test_surcharge_env_visible_sur_le_fil(echo_server, monkeypatch):
    monkeypatch.setenv("HKCONSEILS_MAX_TOKENS", "8000")
    base_url, captured = echo_server
    _build(base_url=base_url).invoke("ping")
    assert _wire_cap(captured["body"]) == 8000


def test_valeur_appelant_prioritaire_sur_le_fil(echo_server, monkeypatch):
    """vision_analyst passe max_tokens=8000 : ni le defaut ni l'env ne doivent l'ecraser."""
    monkeypatch.setenv("HKCONSEILS_MAX_TOKENS", "12000")
    base_url, captured = echo_server
    _build(base_url=base_url, max_tokens=8000).invoke("ping")
    assert _wire_cap(captured["body"]) == 8000


def test_plafond_applique_par_defaut():
    assert _build().max_tokens == 16000


def test_surcharge_par_environnement(monkeypatch):
    monkeypatch.setenv("HKCONSEILS_MAX_TOKENS", "8000")
    assert _build().max_tokens == 8000


def test_valeur_explicite_de_l_appelant_prioritaire():
    assert _build(max_tokens=8000).max_tokens == 8000


def test_responses_api_native_non_plafonnee(monkeypatch):
    """La Responses API (OpenAI natif) utilise max_output_tokens : ne pas y injecter max_tokens."""
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder-not-a-secret")
    llm = OpenAIClient("gpt-5.5", None, provider="openai").get_llm()
    assert llm.max_tokens is None


def test_provider_openai_derriere_un_proxy_reste_plafonne(monkeypatch):
    """base_url custom -> Chat Completions -> le plafond doit s'appliquer (#1024)."""
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder-not-a-secret")
    llm = OpenAIClient("qwen38-27b", BASE_URL, provider="openai").get_llm()
    assert llm.max_tokens == 16000
