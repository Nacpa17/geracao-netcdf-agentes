"""Clientes de modelo de linguagem para o agente — sem dependência nova.

O agente não depende de um modelo específico. Escolha pela opção --llm:

    roteiro:<arquivo.json | arquivo.py | pasta>
        respostas gravadas, em ordem (testes e demonstração sem GPU/API)
    hf:<id do Hugging Face ou pasta local>
        modelo local via transformers (Colab/GPU). Ex.: hf:Qwen/Qwen2.5-Coder-7B-Instruct
    openai:<modelo>[@<base_url>]
        qualquer API compatível com a da OpenAI (OpenAI, vLLM, Ollama, LM Studio...).
        Ex.: openai:qwen2.5-coder:7b@http://localhost:11434/v1   (Ollama)
    anthropic:<modelo>
        API da Anthropic (Claude). Ex.: anthropic:claude-sonnet-5-5

Chaves de API só por variável de ambiente (OPENAI_API_KEY, ANTHROPIC_API_KEY),
nunca em arquivo do projeto.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

Mensagem = dict  # {"role": "system" | "user" | "assistant", "content": str}


class ClienteLLM:
    nome = "llm"

    def conversar(self, mensagens: list[Mensagem]) -> str:  # pragma: no cover
        raise NotImplementedError


class ClienteRoteirizado(ClienteLLM):
    """Devolve respostas prontas, em ordem (ou as calcula com uma função).
    Guarda as conversas recebidas em `chamadas` (útil nos testes)."""

    def __init__(self, respostas: list[str] | Callable[[list[Mensagem]], str],
                 nome: str = "roteiro"):
        self.respostas = respostas
        self.nome = nome
        self.chamadas: list[list[Mensagem]] = []

    def conversar(self, mensagens: list[Mensagem]) -> str:
        self.chamadas.append([dict(m) for m in mensagens])
        if callable(self.respostas):
            return self.respostas(mensagens)
        i = len(self.chamadas) - 1
        if i >= len(self.respostas):
            raise RuntimeError(f"o roteiro só tem {len(self.respostas)} resposta(s)")
        return self.respostas[i]

    @classmethod
    def de_caminho(cls, caminho: str | Path) -> "ClienteRoteirizado":
        """.json (lista de textos), .py/.md/.txt (uma resposta) ou pasta
        (cada arquivo, em ordem alfabética, é uma resposta)."""
        p = Path(caminho)
        if p.is_dir():
            arquivos = sorted(x for x in p.iterdir() if x.is_file()
                              and x.suffix in (".py", ".md", ".txt"))
            return cls([_resposta_de_arquivo(x) for x in arquivos], nome=f"roteiro:{p.name}")
        if p.suffix == ".json":
            dados = json.loads(p.read_text(encoding="utf-8"))
            return cls([str(x) for x in dados], nome=f"roteiro:{p.name}")
        return cls([_resposta_de_arquivo(p)], nome=f"roteiro:{p.name}")


def _resposta_de_arquivo(p: Path) -> str:
    texto = p.read_text(encoding="utf-8")
    return f"```python\n{texto}\n```" if p.suffix == ".py" else texto


def _post_json(url: str, payload: dict, headers: dict, timeout: float,
               tentativas: int = 3) -> dict:
    import requests

    for i in range(tentativas):
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        if r.status_code in (429, 500, 502, 503, 504, 529) and i < tentativas - 1:
            time.sleep(5 * (i + 1))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} de {url}: {r.text[:500]}")
        return r.json()
    raise RuntimeError(f"sem resposta de {url}")  # pragma: no cover


class ClienteOpenAI(ClienteLLM):
    """API /chat/completions (OpenAI e compatíveis: vLLM, Ollama, LM Studio)."""

    def __init__(self, modelo: str, base_url: str = "https://api.openai.com/v1",
                 chave_env: str = "OPENAI_API_KEY", temperatura: float | None = None,
                 max_tokens: int = 8192, timeout: float = 900):
        self.modelo, self.base_url, self.chave_env = modelo, base_url.rstrip("/"), chave_env
        self.temperatura, self.max_tokens, self.timeout = temperatura, max_tokens, timeout
        self.nome = f"openai:{modelo}"
        self._campo_tokens = "max_tokens"

    def conversar(self, mensagens: list[Mensagem]) -> str:
        headers = {"Content-Type": "application/json"}
        chave = os.environ.get(self.chave_env)
        if chave:
            headers["Authorization"] = f"Bearer {chave}"
        payload = {"model": self.modelo, "messages": mensagens,
                   self._campo_tokens: self.max_tokens}
        if self.temperatura is not None:
            payload["temperature"] = self.temperatura
        url = f"{self.base_url}/chat/completions"
        try:
            dados = _post_json(url, payload, headers, self.timeout)
        except RuntimeError as e:
            # modelos novos da OpenAI recusam max_tokens
            if "max_completion_tokens" not in str(e) or self._campo_tokens != "max_tokens":
                raise
            self._campo_tokens = "max_completion_tokens"
            payload["max_completion_tokens"] = payload.pop("max_tokens")
            dados = _post_json(url, payload, headers, self.timeout)
        return dados["choices"][0]["message"]["content"] or ""


class ClienteAnthropic(ClienteLLM):
    """API de mensagens da Anthropic (sem o SDK: só requests)."""

    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, modelo: str, chave_env: str = "ANTHROPIC_API_KEY",
                 temperatura: float | None = None, max_tokens: int = 8192,
                 timeout: float = 900):
        self.modelo, self.chave_env = modelo, chave_env
        self.temperatura, self.max_tokens, self.timeout = temperatura, max_tokens, timeout
        self.nome = f"anthropic:{modelo}"

    def conversar(self, mensagens: list[Mensagem]) -> str:
        chave = os.environ.get(self.chave_env)
        if not chave:
            raise RuntimeError(f"defina a variável de ambiente {self.chave_env}")
        sistema = "\n\n".join(m["content"] for m in mensagens if m["role"] == "system")
        payload = {"model": self.modelo, "max_tokens": self.max_tokens,
                   "messages": [m for m in mensagens if m["role"] != "system"]}
        if sistema:
            payload["system"] = sistema
        if self.temperatura is not None:
            payload["temperature"] = self.temperatura
        headers = {"x-api-key": chave, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        dados = _post_json(self.URL, payload, headers, self.timeout)
        return "".join(b.get("text", "") for b in dados.get("content", [])
                       if b.get("type") == "text")


class ClienteTransformers(ClienteLLM):
    """Modelo local (Hugging Face). Carrega na primeira chamada.

    Numa A100, modelos de código de 7B-14B rodam em bf16; numa T4 use
    quantizar_4bit=True (precisa de bitsandbytes)."""

    def __init__(self, modelo: str, max_new_tokens: int = 4096,
                 temperatura: float | None = None, quantizar_4bit: bool = False):
        self.modelo, self.max_new_tokens = modelo, max_new_tokens
        self.temperatura, self.quantizar_4bit = temperatura, quantizar_4bit
        self.nome = f"hf:{modelo}"
        self._tok = self._mod = None

    def _carregar(self):
        import torch
        import transformers
        from transformers import AutoModelForCausalLM, AutoTokenizer

        kwargs = {"device_map": "auto"}
        # transformers 5 trocou torch_dtype por dtype
        chave = "dtype" if int(transformers.__version__.split(".")[0]) >= 5 else "torch_dtype"
        kwargs[chave] = "auto"
        if self.quantizar_4bit:
            from transformers import BitsAndBytesConfig
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16)
        self._tok = AutoTokenizer.from_pretrained(self.modelo)
        self._mod = AutoModelForCausalLM.from_pretrained(self.modelo, **kwargs)
        self._mod.eval()

    def _prompt(self, mensagens: list[Mensagem]) -> str:
        try:
            return self._tok.apply_chat_template(mensagens, tokenize=False,
                                                 add_generation_prompt=True)
        except Exception:  # noqa: BLE001 - modelos cujo template não aceita "system"
            sistema = "\n\n".join(m["content"] for m in mensagens if m["role"] == "system")
            resto = [dict(m) for m in mensagens if m["role"] != "system"]
            if sistema and resto:
                resto[0]["content"] = sistema + "\n\n" + resto[0]["content"]
            return self._tok.apply_chat_template(resto, tokenize=False,
                                                 add_generation_prompt=True)

    def conversar(self, mensagens: list[Mensagem]) -> str:
        import torch

        if self._mod is None:
            self._carregar()
        texto = self._prompt(mensagens)
        entrada = self._tok(texto, return_tensors="pt", add_special_tokens=False)
        entrada = {k: v.to(self._mod.device) for k, v in entrada.items()}
        amostrar = bool(self.temperatura)
        extra = {"temperature": self.temperatura} if amostrar else {}
        pad = self._tok.pad_token_id
        if pad is None:
            pad = self._tok.eos_token_id
        with torch.inference_mode():
            saida = self._mod.generate(**entrada, max_new_tokens=self.max_new_tokens,
                                       do_sample=amostrar, pad_token_id=pad, **extra)
        novos = saida[0, entrada["input_ids"].shape[-1]:]
        return self._tok.decode(novos, skip_special_tokens=True)


def criar_cliente(spec: str, temperatura: float | None = None,
                  max_tokens: int | None = None, quantizar_4bit: bool = False) -> ClienteLLM:
    """Cria o cliente a partir do texto da opção --llm (ver docstring do módulo)."""
    tipo, _, resto = spec.partition(":")
    if not resto:
        raise ValueError(f"--llm inválido: {spec!r} (ex.: hf:Qwen/Qwen2.5-Coder-7B-Instruct)")
    tokens = {"max_tokens": max_tokens} if max_tokens else {}
    if tipo == "roteiro":
        return ClienteRoteirizado.de_caminho(resto)
    if tipo == "hf":
        return ClienteTransformers(resto, temperatura=temperatura,
                                   quantizar_4bit=quantizar_4bit,
                                   **({"max_new_tokens": max_tokens} if max_tokens else {}))
    if tipo == "openai":
        modelo, _, base = resto.partition("@")
        return ClienteOpenAI(modelo, base_url=base or "https://api.openai.com/v1",
                             temperatura=temperatura, **tokens)
    if tipo == "anthropic":
        return ClienteAnthropic(resto, temperatura=temperatura, **tokens)
    raise ValueError(f"tipo de --llm desconhecido: {tipo!r} (use roteiro, hf, openai ou anthropic)")
