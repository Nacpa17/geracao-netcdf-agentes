"""Agente gerador de plug-ins: extração, checagem estática, laço de correção."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

netCDF4 = pytest.importorskip("netCDF4")
np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

from geracao_netcdf.agente import (PontoControle, Tarefa, ambiente_limpo, checar_codigo,  # noqa: E402
                                conferir_pontos, extrair_codigo, montar_prompt,
                                rodar_agente)
from geracao_netcdf.aparelhos import caminho_do_aparelho  # noqa: E402
from geracao_netcdf.llm import ClienteRoteirizado  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PASTA = caminho_do_aparelho("rd80")
CERTO = "from geracao_netcdf.aparelhos.rd80 import calcular, dimensoes, ler\n"


def _tarefa(amostras, **kw):
    base = dict(nome="teste_rd80", descricao="RD-80, formato DISDRODATA.",
                amostras=list(amostras), esquema=PASTA / "netCDF_info.json",
                constantes=PASTA / "variables_info.json", max_iteracoes=5, timeout_s=60)
    base.update(kw)
    return Tarefa(**base)


def _bloco(codigo):
    return f"Aqui está:\n```python\n{codigo}```\nPronto."


# --- extração e checagem estática ----------------------------------------

def test_extrair_codigo():
    assert extrair_codigo(_bloco(CERTO)) == CERTO
    duas = "```python\nx = 1\n```\n```python\ndef ler(a, c):\n    pass\ndef calcular(g, c):\n    pass\n```"
    assert extrair_codigo(duas).startswith("def ler")
    assert "def ler" in extrair_codigo("```python\ndef ler(a, c):\n    pass\n")   # sem fechar
    assert extrair_codigo("def ler(a, c):\n    return 1\n").startswith("def ler")
    assert extrair_codigo("não sei fazer isso") is None
    assert extrair_codigo("```Python\ndef ler(a, c): pass\ndef calcular(g, c): pass\n```")


@pytest.mark.parametrize("codigo, trecho", [
    ("import os\n", "import não permitido: os"),
    ("from subprocess import run\n", "subprocess"),
    ("from . import x\n", "from ."),
    ("def ler(a, c):\n    open(a, 'w')\n", "só pode LER"),
    ("def ler(a, c):\n    open(a, modo)\n", "texto literal"),
    ("from pathlib import Path\ndef ler(a, c):\n    Path(a).open('a')\n", "só pode LER"),
    ("def ler(a, c):\n    c.to_csv('x.csv')\n", "to_csv"),
    ("def ler(a, c):\n    eval('1')\n", "`eval`"),
    ("def ler(a, c):\n    getattr(c, 'x')\n", "`getattr`"),
    ("def ler(a, c):\n    a.__class__\n", "__class__"),
    ("def ler(a, c)\n", "sintaxe"),
])
def test_checagem_estatica_barra(codigo, trecho):
    problemas = checar_codigo(codigo)
    assert any(trecho in p for p in problemas), problemas


def test_checagem_estatica_aceita_codigo_normal():
    codigo = ("import io\nimport numpy as np\nimport pandas as pd\n"
              "from geracao_netcdf.aparelhos.rd80 import calcular, dimensoes\n"
              "def ler(arquivos, const):\n"
              "    with open(arquivos[0], encoding='latin-1') as f:\n"
              "        texto = f.read().replace(',', '.')\n"
              "    df = pd.read_csv(io.StringIO(texto), sep='\\t').rename(columns={'a': 'b'})\n"
              "    return df\n")
    assert checar_codigo(codigo) == []
    assert checar_codigo("def ler(a, c): pass\n") == ["falta a função `dimensoes` do contrato",
                                                      "falta a função `calcular` do contrato"]
    for p in sorted((ROOT / "benchmarks" / "geracao" / "solucoes").rglob("*.py")):
        assert checar_codigo(p.read_text(encoding="utf-8")) == [], p.name


def test_ambiente_sem_chaves(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "segredo")
    monkeypatch.setenv("HF_TOKEN", "segredo")
    env = ambiente_limpo()
    assert "ANTHROPIC_API_KEY" not in env and "HF_TOKEN" not in env
    assert env.get("PATH") and env["PYTHONNOUSERSITE"] == "1"


# --- prompt e tarefa -------------------------------------------------------

def test_prompt_tem_o_que_o_modelo_precisa(rd80_brutos):
    t = _tarefa(rd80_brutos, exemplo="rd80", documentacao=PASTA / "DOCUMENTACAO.md",
                pontos_de_controle=[PontoControle("2024-12-15 14:00:00", "rain_rate", 1.0)])
    p = montar_prompt(t)
    for trecho in ("def ler(arquivos", "`base_time` escalar int64", "o núcleo cria — NÃO devolva",
                   "valor fixo do esquema (-2.1815896)", "bit_2_description",
                   '"sensor_area": 0.005', "\\t", "CRLF", "RAT [mm]",
                   "from geracao_netcdf.aparelhos.rd80 import", "Marshall-Palmer",
                   "rain_rate em 2024-12-15 14:00:00 UTC = 1.0"):
        assert trecho in p, trecho
    sem = montar_prompt(_tarefa(rd80_brutos))
    assert "Exemplo:" not in sem and "Documentação do aparelho" not in sem


def test_tarefa_de_yaml(tmp_path, rd80_brutos):
    pasta = tmp_path / "amostras"
    pasta.mkdir()
    for a in rd80_brutos:
        (pasta / a.name).write_bytes(a.read_bytes())
    y = tmp_path / "tarefa.yaml"
    y.write_text("nome: meu_rd80\ndescricao: teste\namostras: amostras\naparelho_base: rd80\n"
                 "pontos_de_controle:\n  - {tempo: '2024-12-15 14:00:00', variavel: rain_rate,"
                 " valor: 1.5}\nmax_iteracoes: 2\n", encoding="utf-8")
    t = Tarefa.de_yaml(y)
    assert [a.name for a in t.amostras] == sorted(a.name for a in rd80_brutos)
    assert t.esquema == PASTA / "netCDF_info.json" and t.max_iteracoes == 2
    assert t.pontos_de_controle[0].valor == 1.5
    y.write_text("nome: x\namostras: amostras\n", encoding="utf-8")
    with pytest.raises(ValueError, match="esquema"):
        Tarefa.de_yaml(y)


# --- pontos de controle ----------------------------------------------------

def _maior_chuva(arquivo):
    with netCDF4.Dataset(arquivo) as nc:
        nc.set_auto_mask(False)
        rr = np.asarray(nc["rain_rate"][:], dtype=float)
        i = int(np.argmax(rr))
        return pd.Timestamp("2024-12-15") + pd.Timedelta(seconds=float(nc["time"][i])), rr[i]


def test_pontos_de_controle(rd80_netcdf):
    quando, valor = _maior_chuva(rd80_netcdf[0])
    arquivos = [str(p) for p in rd80_netcdf]
    certo = PontoControle(f"{quando:%Y-%m-%d %H:%M:%S}", "rain_rate", round(valor, 4))
    assert conferir_pontos(arquivos, [certo]) == []
    utc = PontoControle(f"{quando:%Y-%m-%dT%H:%M:%S}Z", "rain_rate", round(valor, 4))
    assert conferir_pontos(arquivos, [utc]) == []
    deslocado = PontoControle(f"{quando + pd.Timedelta(hours=4):%Y-%m-%d %H:%M:%S}",
                              "rain_rate", round(valor, 4))
    falhas = conferir_pontos(arquivos, [deslocado])
    assert "4 h antes" in falhas[0] and falhas[-1].startswith("Resumo")
    assert "ausente" in conferir_pontos(arquivos, [PontoControle(certo.tempo, "nao_existe", 1)])[0]


# --- o laço ---------------------------------------------------------------

def test_laco_corrige_ate_passar(rd80_brutos, tmp_path):
    respostas = ["Não sei fazer isso.",
                 _bloco("def ler(arquivos, const:\n    pass\n"),
                 _bloco("from geracao_netcdf.aparelhos.rd80 import calcular, dimensoes\n"
                        "def ler(arquivos, const):\n"
                        "    raise ValueError('coluna desconhecida: XYZ')\n"),
                 _bloco(CERTO)]
    cli = ClienteRoteirizado(respostas)
    res = rodar_agente(_tarefa(rd80_brutos), cli, tmp_path, log=lambda *a: None)
    assert res.sucesso and res.iteracoes == 4
    assert [t.etapa for t in res.tentativas] == ["extracao", "estatico", "execucao", "ok"]
    assert "bloco ```python" in cli.chamadas[1][-1]["content"]
    assert "erro de sintaxe" in cli.chamadas[2][-1]["content"]
    assert "coluna desconhecida: XYZ" in cli.chamadas[3][-1]["content"]
    assert len(cli.chamadas[3]) == 4                    # histórico "ultima"
    assert Path(res.plugin_final).read_text() == CERTO
    assert len(res.arquivos_finais) == 2
    rel = json.loads((tmp_path / "relatorio.json").read_text(encoding="utf-8"))
    assert rel["sucesso"] and rel["iteracoes"] == 4
    assert (tmp_path / "prompt.md").exists() and (tmp_path / "retorno_3.md").exists()


def test_laco_historico_completo_e_validacao(rd80_brutos, tmp_path):
    ruim = ("from geracao_netcdf.aparelhos.rd80 import dimensoes, ler\n"
            "from geracao_netcdf.aparelhos.rd80 import calcular as calc\n"
            "def calcular(grade, const):\n"
            "    r = calc(grade, const)\n"
            "    r['qc_number_detected_particles'] = r['qc_number_detected_particles'] | 16\n"
            "    return r\n")
    cli = ClienteRoteirizado([_bloco(ruim), _bloco(CERTO)])
    res = rodar_agente(_tarefa(rd80_brutos), cli, tmp_path, historico="completo",
                       log=lambda *a: None)
    assert [t.etapa for t in res.tentativas] == ["validacao", "ok"]
    assert "E_QC_BITS" in cli.chamadas[1][-1]["content"]
    assert "Resumo das variáveis" in cli.chamadas[1][-1]["content"]
    assert len(cli.chamadas[1]) == 4


def test_pontos_de_controle_reprovam_horario_errado(rd80_brutos, rd80_netcdf, tmp_path):
    quando, valor = _maior_chuva(rd80_netcdf[0])
    ponto = PontoControle(f"{quando + pd.Timedelta(hours=4):%Y-%m-%d %H:%M:%S}",
                          "rain_rate", round(valor, 4))
    cli = ClienteRoteirizado([_bloco(CERTO)])
    res = rodar_agente(_tarefa(rd80_brutos, pontos_de_controle=[ponto], max_iteracoes=1),
                       cli, tmp_path, log=lambda *a: None)
    assert not res.sucesso and res.tentativas[0].etapa == "controle"
    assert res.ultimo_plugin_executado and len(res.arquivos_finais) == 2


def test_modelo_falha_e_tempo_limite(rd80_brutos, tmp_path):
    res = rodar_agente(_tarefa(rd80_brutos), ClienteRoteirizado([]), tmp_path / "a",
                       log=lambda *a: None)
    assert not res.sucesso and res.tentativas[0].etapa == "modelo"
    lento = ("from geracao_netcdf.aparelhos.rd80 import calcular, dimensoes\n"
             "def ler(arquivos, const):\n    while True:\n        pass\n")
    res = rodar_agente(_tarefa(rd80_brutos, timeout_s=2, max_iteracoes=1),
                       ClienteRoteirizado([_bloco(lento)]), tmp_path / "b", log=lambda *a: None)
    assert res.tentativas[0].etapa == "execucao"
    assert "tempo limite" in res.tentativas[0].stderr
