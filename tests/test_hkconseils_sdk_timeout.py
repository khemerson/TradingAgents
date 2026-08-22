"""Fork HKCONSEILS - garde-fou sur le delai SDK (option A, GATE 3).

v0.3.1 accepte `timeout` dans _PASSTHROUGH_KWARGS mais rien ne l'y injecte : ni
DEFAULT_CONFIG ni trading_graph._get_provider_kwargs ne portent la cle. Sans le
defaut pose par le fork, le delai retombe sur celui du SDK openai (read=600 s),
sous la valeur calibree sur l'exploitation (1800 s).

Deux niveaux de preuve, comme pour le plafond :
  - le delai atteint le client HTTP reel (objet httpx), pas seulement l'attribut ;
  - il gouverne effectivement la coupure, mesuree contre un serveur lent.
"""

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tradingagents.llm_clients.openai_client import _HK_DEFAULT_SDK_TIMEOUT, OpenAIClient

BASE_URL = "http://127.0.0.1:4000/v1"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("HKCONSEILS_SDK_TIMEOUT", raising=False)
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "placeholder-not-a-secret")


def _build(base_url=BASE_URL, **kwargs):
    return OpenAIClient("qwen38-27b", base_url, provider="openai_compatible", **kwargs).get_llm()


def _wire_timeout(llm):
    """Delai porte par le client httpx reellement utilise pour les appels."""
    return llm.root_client._client.timeout.read


# --------------------------------------------------------------------------
# Serveur lent : ne repond jamais dans le delai imparti
# --------------------------------------------------------------------------

class _SlowHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        time.sleep(30)  # bien au-dela du delai court utilise par le test

    def log_message(self, *args):
        pass


@pytest.fixture
def slow_server():
    srv = HTTPServer(("127.0.0.1", 0), _SlowHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_valeur_calibree_inchangee():
    """1800 s - pas le defaut 600 s du SDK, pas une valeur amont."""
    assert _HK_DEFAULT_SDK_TIMEOUT == 1800.0


def test_defaut_atteint_le_client_http():
    """Preuve sur le fil : c'est l'objet httpx qui porte le delai, pas juste l'attribut."""
    assert _wire_timeout(_build()) == 1800.0


def test_surcharge_par_environnement(monkeypatch):
    monkeypatch.setenv("HKCONSEILS_SDK_TIMEOUT", "900")
    assert _wire_timeout(_build()) == 900.0


def test_valeur_appelant_prioritaire(monkeypatch):
    """setdefault : une valeur explicite ne doit etre ecrasee ni par le defaut ni par l'env."""
    monkeypatch.setenv("HKCONSEILS_SDK_TIMEOUT", "900")
    assert _wire_timeout(_build(timeout=120)) == 120.0


def test_applique_aussi_sous_responses_api(monkeypatch):
    """Le delai est independant du plafond : il vaut aussi pour OpenAI natif."""
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder-not-a-secret")
    llm = OpenAIClient("gpt-5.5", None, provider="openai").get_llm()
    assert _wire_timeout(llm) == 1800.0


def test_le_delai_gouverne_vraiment_la_coupure(slow_server, monkeypatch):
    """Preuve comportementale : contre un serveur muet, la coupure suit le delai configure.

    max_retries=0 pour mesurer une seule tentative (le SDK retente sinon, ce qui
    multiplierait la duree observee).
    """
    monkeypatch.setenv("HKCONSEILS_SDK_TIMEOUT", "2")
    llm = _build(base_url=slow_server, max_retries=0)
    assert _wire_timeout(llm) == 2.0

    t0 = time.time()
    with pytest.raises(Exception) as excinfo:
        llm.invoke("ping")
    elapsed = time.time() - t0

    assert "timeout" in type(excinfo.value).__name__.lower() or "timeout" in str(excinfo.value).lower(), (
        f"attendu une expiration, recu {type(excinfo.value).__name__}: {excinfo.value}"
    )
    # La coupure suit le delai demande (2 s), et non le defaut du SDK (600 s).
    assert elapsed < 15, f"coupure apres {elapsed:.1f}s : le delai configure ne gouverne pas le transport"
