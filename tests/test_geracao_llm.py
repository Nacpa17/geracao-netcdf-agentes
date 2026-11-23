"""Clientes de modelo de linguagem (sem rede: servidores falsos em 127.0.0.1)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from geracao_netcdf import llm
from geracao_netcdf.llm import (ClienteAnthropic, ClienteOpenAI, ClienteRoteirizado,
                             ClienteTransformers, criar_cliente)


@pytest.fixture
def servidor(monkeypatch):
    """Servidor HTTP falso: devolve as respostas da lista, em ordem, e guarda
    o que recebeu."""
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    estado = {"respostas": [], "recebidos": []}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            estado["recebidos"].append({"caminho": self.path, "headers": dict(self.headers),
                                        "json": corpo})
            status, resposta = estado["respostas"].pop(0)
            dados = json.dumps(resposta).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    estado["url"] = f"http://127.0.0.1:{srv.server_address[1]}"
    yield estado
    srv.shutdown()


MSGS = [{"role": "system", "content": "S"}, {"role": "user", "content": "oi"}]


def test_openai_compativel_e_troca_max_tokens(servidor, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    servidor["respostas"] += [
        (400, {"error": {"message": "Unsupported parameter: 'max_tokens'. "
                                    "Use 'max_completion_tokens' instead."}}),
        (200, {"choices": [{"message": {"content": "resposta"}}]})]
    c = ClienteOpenAI("modelo-x", base_url=servidor["url"] + "/v1", temperatura=0.0)
    assert c.conversar(MSGS) == "resposta"
    r1, r2 = servidor["recebidos"]
    assert r1["caminho"] == "/v1/chat/completions" and "max_tokens" in r1["json"]
    assert "max_completion_tokens" in r2["json"] and "max_tokens" not in r2["json"]
    assert r2["headers"]["Authorization"] == "Bearer sk-teste"
    assert r2["json"]["messages"] == MSGS and r2["json"]["temperature"] == 0.0


def test_openai_sem_chave_e_repete_em_503(servidor, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    servidor["respostas"] += [(503, {"erro": "ocupado"}),
                              (200, {"choices": [{"message": {"content": "ok"}}]})]
    c = ClienteOpenAI("m", base_url=servidor["url"])
    assert c.conversar(MSGS) == "ok"
    assert "Authorization" not in servidor["recebidos"][1]["headers"]
    assert "temperature" not in servidor["recebidos"][1]["json"]
    servidor["respostas"].append((401, {"erro": "sem permissão"}))
    with pytest.raises(RuntimeError, match="HTTP 401"):
        c.conversar(MSGS)


def test_anthropic(servidor, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "chave")
    servidor["respostas"].append((200, {"content": [{"type": "text", "text": "oi"},
                                                    {"type": "text", "text": " mundo"}]}))
    c = ClienteAnthropic("claude-x")
    c.URL = servidor["url"] + "/v1/messages"
    assert c.conversar(MSGS) == "oi mundo"
    r = servidor["recebidos"][0]
    assert r["headers"]["x-api-key"] == "chave" and r["headers"]["anthropic-version"]
    assert r["json"]["system"] == "S" and r["json"]["messages"] == MSGS[1:]
    assert r["json"]["model"] == "claude-x" and "temperature" not in r["json"]
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        c.conversar(MSGS)


def test_roteiro(tmp_path):
    c = ClienteRoteirizado(["a", "b"])
    assert c.conversar(MSGS) == "a" and c.conversar(MSGS) == "b"
    with pytest.raises(RuntimeError):
        c.conversar(MSGS)
    assert len(c.chamadas) == 3
    (tmp_path / "2_b.py").write_text("x = 2\n")
    (tmp_path / "1_a.md").write_text("texto")
    d = ClienteRoteirizado.de_caminho(tmp_path)
    assert d.respostas == ["texto", "```python\nx = 2\n\n```"]
    j = tmp_path / "r.json"
    j.write_text(json.dumps(["um", "dois"]))
    assert ClienteRoteirizado.de_caminho(j).respostas == ["um", "dois"]
    f = ClienteRoteirizado(lambda m: m[-1]["content"].upper())
    assert f.conversar(MSGS) == "OI"


def test_criar_cliente():
    c = criar_cliente("openai:qwen2.5-coder:7b@http://localhost:11434/v1")
    assert isinstance(c, ClienteOpenAI) and c.modelo == "qwen2.5-coder:7b"
    assert c.base_url == "http://localhost:11434/v1"
    assert criar_cliente("openai:gpt-x").base_url == "https://api.openai.com/v1"
    a = criar_cliente("anthropic:claude-x", max_tokens=100, temperatura=0.2)
    assert isinstance(a, ClienteAnthropic) and a.max_tokens == 100 and a.temperatura == 0.2
    h = criar_cliente("hf:Qwen/Qwen2.5-Coder-7B-Instruct", quantizar_4bit=True)
    assert isinstance(h, ClienteTransformers) and h.quantizar_4bit and h._mod is None
    for ruim in ("qwen", "xyz:modelo"):
        with pytest.raises(ValueError):
            criar_cliente(ruim)
