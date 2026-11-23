"""Benchmark do agente: o MESMO dado de RD-80 escrito em vários formatos.

Para cada formato o agente recebe a descrição do formato, amostras, esquema
e constantes, e escreve um plug-in. A nota sai da comparação dos NetCDFs
gerados com o gabarito, variável por variável (o gabarito é o formato
original lido pelo plug-in rd80, que reproduz a ARM e o RD80-Processing).

Modos (o que o prompt traz além do formato):
  com_exemplo   o código do plug-in rd80 (lê outro formato): mede adaptar o leitor
  do_zero       só a documentação do RD-80 (fórmulas), sem código

Uso:
  # sanidade do circuito, sem modelo (respostas gravadas): tem de dar 100%
  python -m geracao_netcdf.bench --llm roteiro:benchmarks/geracao/solucoes

  # modelo de verdade (Colab com GPU)
  python -m geracao_netcdf.bench --llm hf:Qwen/Qwen2.5-Coder-7B-Instruct --modo com_exemplo
  python -m geracao_netcdf.bench --llm anthropic:claude-sonnet-5-5 --modo do_zero

Resultados em benchmarks/results/geracao_<modo>_<data>/ (resumo.md e resumo.json).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np

from geracao_netcdf.agente import RAIZ, PontoControle, Tarefa, rodar_agente
from geracao_netcdf.aparelhos import carregar
from geracao_netcdf.esquema import carregar_constantes, carregar_esquema
from geracao_netcdf.gerar import gerar
from geracao_netcdf.llm import ClienteLLM, ClienteRoteirizado, criar_cliente
from geracao_netcdf.sintetico import (PASTA_RD80, VARIANTES, constantes_da_variante,
                                   escrever_variante, simular_minutos)
from geracao_netcdf.validador import validar

ESQUEMA_RD80 = PASTA_RD80 / "netCDF_info.json"
DOC_RD80 = PASTA_RD80 / "DOCUMENTACAO.md"


def preparar(pasta: Path, n_minutos: int = 2880, minutos_por_arquivo: int = 1440,
             semente: int = 7, inicio: str = "2024-12-15 13:53:00") -> dict[str, Any]:
    """Dados sintéticos em todos os formatos + gabarito + pontos de controle."""
    pasta.mkdir(parents=True, exist_ok=True)
    for sub in ("dados", "constantes", "gabarito", "agente"):     # rodada anterior
        shutil.rmtree(pasta / sub, ignore_errors=True)
    df = simular_minutos(inicio=inicio, n_minutos=n_minutos, semente=semente)
    dados: dict[str, list[str]] = {}
    constantes: dict[str, str] = {}
    for nome in VARIANTES:
        arquivos = escrever_variante(nome, df, pasta / "dados" / nome, minutos_por_arquivo)
        dados[nome] = [str(a) for a in arquivos]
        constantes[nome] = str(constantes_da_variante(nome, pasta / "constantes" / f"{nome}.json"))
    gab = pasta / "gabarito"
    gerar(carregar("rd80"), [Path(a) for a in dados["original"]],
          carregar_esquema(ESQUEMA_RD80), carregar_constantes(constantes["original"]),
          gab, linha_de_comando="geracao_netcdf.bench (gabarito)")
    prep = {"pasta": str(pasta), "dados": dados, "constantes": constantes,
            "gabarito": str(gab), "pontos_de_controle": pontos_de_controle(gab),
            "n_minutos": n_minutos, "semente": semente}
    (pasta / "preparo.json").write_text(json.dumps(prep, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    return prep


def pontos_de_controle(gabarito: Path, n: int = 3) -> list[dict[str, Any]]:
    """Os minutos de maior chuva, separados por >= 6 h: rain_rate (como o
    aparelho mostra, 4 casas) e number_detected_drops."""
    candidatos = []
    for f in sorted(gabarito.glob("*.nc")):
        with netCDF4.Dataset(f) as nc:
            nc.set_auto_mask(False)
            ref = nc["time"].units.split("since")[1].strip()[:10]
            t = np.asarray(nc["time"][:], dtype=float)
            r = np.asarray(nc["rain_rate"][:], dtype=float)
            tot = np.asarray(nc["number_detected_drops"][:], dtype=float)
            for i in np.argsort(-np.where(r > 0, r, -1))[:200]:
                if r[i] > 0:
                    quando = np.datetime64(ref) + np.timedelta64(int(t[i]), "s")
                    candidatos.append((float(r[i]), quando, float(tot[i])))
    candidatos.sort(key=lambda x: -x[0])
    escolhidos: list[tuple] = []
    for c in candidatos:
        if all(abs((c[1] - e[1]) / np.timedelta64(1, "h")) >= 6 for e in escolhidos):
            escolhidos.append(c)
        if len(escolhidos) == n:
            break
    pontos = []
    for taxa, quando, total in sorted(escolhidos, key=lambda x: x[1]):
        tempo = str(quando).replace("T", " ")
        pontos.append({"tempo": tempo, "variavel": "rain_rate", "valor": round(taxa, 4)})
        pontos.append({"tempo": tempo, "variavel": "number_detected_drops", "valor": total,
                       "tolerancia": 0.5, "tolerancia_rel": 0.0})
    return pontos


def avaliar(arquivos: list[str], gabarito: Path) -> dict[str, Any]:
    """Compara os NetCDFs gerados com o gabarito (dias e variáveis)."""
    esquema = carregar_esquema(ESQUEMA_RD80)
    esperados = sorted(p.name for p in gabarito.glob("*.nc"))
    gerados = {Path(a).name: a for a in arquivos}
    faltando = [n for n in esperados if n not in gerados]
    extras = sorted(n for n in gerados if n not in esperados)
    identicas = comparadas = erros_esquema = erros_ref = 0
    diferentes: dict[str, Any] = {}
    for nome in esperados:
        if nome not in gerados:
            continue
        r = validar(gerados[nome], esquema, referencia=gabarito / nome)
        erros_esquema += sum(1 for p in r.erros if not p.codigo.startswith("R_"))
        erros_ref += sum(1 for p in r.erros if p.codigo.startswith("R_"))
        if r.comparacao:
            identicas += r.comparacao["identicas"]
            comparadas += r.comparacao["comparadas"]
            if r.comparacao["diferentes"]:
                diferentes[nome] = r.comparacao["diferentes"]
    correto = bool(arquivos) and not faltando and not extras and not erros_esquema \
        and not erros_ref and identicas == comparadas
    return {"correto": correto, "dias_esperados": len(esperados), "faltando": faltando,
            "extras": extras, "variaveis_identicas": identicas,
            "variaveis_comparadas": comparadas, "erros_esquema": erros_esquema,
            "erros_referencia": erros_ref, "diferentes": diferentes}


def _cliente_roteiro(pasta: Path, variante: str) -> ClienteLLM:
    sub, arq = pasta / variante, pasta / f"{variante}.py"
    if sub.is_dir():
        return ClienteRoteirizado.de_caminho(sub)
    if arq.exists():
        return ClienteRoteirizado.de_caminho(arq)
    return ClienteRoteirizado(["(sem resposta gravada para esta variante)"])


def rodar(llm: str, modo: str = "com_exemplo", variantes: list[str] | None = None,
          pasta: str | Path | None = None, max_iteracoes: int = 5, controle: bool = True,
          historico: str = "ultima", n_minutos: int = 2880, minutos_por_arquivo: int = 1440,
          cliente: ClienteLLM | None = None, log=print, **kw_cliente) -> dict[str, Any]:
    if modo not in ("com_exemplo", "do_zero"):
        raise ValueError("modo deve ser com_exemplo ou do_zero")
    variantes = variantes or list(VARIANTES)
    desconhecidas = [v for v in variantes if v not in VARIANTES]
    if desconhecidas:
        raise ValueError(f"variantes desconhecidas: {desconhecidas}; existem {list(VARIANTES)}")
    pasta = Path(pasta or RAIZ / "benchmarks" / "results"
                 / f"geracao_{modo}_{datetime.now():%Y%m%d_%H%M%S}").resolve()
    prep = preparar(pasta, n_minutos=n_minutos, minutos_por_arquivo=minutos_por_arquivo)
    gab = Path(prep["gabarito"])
    pontos = [PontoControle(**p) for p in prep["pontos_de_controle"]] if controle else []
    roteiro = Path(llm.partition(":")[2]) if llm.startswith("roteiro:") else None
    if cliente is None and roteiro is None:
        cliente = criar_cliente(llm, **kw_cliente)      # carregado uma vez só
    linhas = []
    for v in variantes:
        tarefa = Tarefa(
            nome=f"rd80_{v}", amostras=[Path(a) for a in prep["dados"][v]],
            descricao=("Disdrômetro Joss-Waldvogel RD-80 (20 classes), integração de 60 s. "
                       "Formato dos arquivos: " + VARIANTES[v].descricao),
            esquema=ESQUEMA_RD80, constantes=Path(prep["constantes"][v]),
            documentacao=DOC_RD80 if modo == "do_zero" else None,
            exemplo="rd80" if modo == "com_exemplo" else None,
            pontos_de_controle=pontos, max_iteracoes=max_iteracoes)
        cli = _cliente_roteiro(roteiro, v) if roteiro else cliente
        t0 = time.time()
        res = rodar_agente(tarefa, cli, pasta / "agente" / v, historico=historico, log=log)
        usado = res.plugin_final or res.ultimo_plugin_executado
        aval = avaliar(res.arquivos_finais, gab)
        linhas.append({
            "variante": v, "agente_ok": res.sucesso, "correto": aval["correto"],
            "iteracoes": res.iteracoes, "etapa_final": res.tentativas[-1].etapa,
            "variaveis": f"{aval['variaveis_identicas']}/{aval['variaveis_comparadas']}",
            "dias": f"{aval['dias_esperados'] - len(aval['faltando'])}/{aval['dias_esperados']}",
            "tempo_modelo_s": round(sum(t.duracao_llm_s for t in res.tentativas), 1),
            "tempo_total_s": round(time.time() - t0, 1), "avaliacao": aval,
            "plugin": usado})
        log(f"== {v}: {'CORRETO' if aval['correto'] else 'ERRADO'} "
            f"({linhas[-1]['variaveis']} variáveis, {res.iteracoes} tentativa(s))")
    n_ok = sum(x["correto"] for x in linhas)
    resumo = {"llm": llm, "modo": modo, "controle": controle, "historico": historico,
              "max_iteracoes": max_iteracoes, "variantes": len(linhas), "corretas": n_ok,
              "taxa_acerto": round(n_ok / len(linhas), 3) if linhas else 0.0,
              "acerto_na_1a": sum(x["correto"] and x["iteracoes"] == 1 for x in linhas),
              "media_iteracoes": round(float(np.mean([x["iteracoes"] for x in linhas])), 2),
              "pasta": str(pasta), "linhas": linhas}
    (pasta / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    (pasta / "resumo.md").write_text(_markdown(resumo), encoding="utf-8")
    return resumo


def _markdown(r: dict[str, Any]) -> str:
    linhas = [f"# Benchmark de geração — {r['llm']} ({r['modo']})", "",
              f"Corretas: **{r['corretas']}/{r['variantes']}** "
              f"({100 * r['taxa_acerto']:.0f}%), {r['acerto_na_1a']} na 1ª tentativa; "
              f"média de {r['media_iteracoes']} tentativa(s) (máx. {r['max_iteracoes']}). "
              f"Pontos de controle: {'sim' if r['controle'] else 'não'}.", "",
              "| formato | correto | tentativas | etapa final | variáveis iguais | dias | "
              "tempo do modelo (s) |", "|---|---|---|---|---|---|---|"]
    for x in r["linhas"]:
        linhas.append(f"| {x['variante']} | {'sim' if x['correto'] else 'não'} | "
                      f"{x['iteracoes']} | {x['etapa_final']} | {x['variaveis']} | "
                      f"{x['dias']} | {x['tempo_modelo_s']} |")
    return "\n".join(linhas) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--llm", required=True)
    ap.add_argument("--modo", choices=["com_exemplo", "do_zero"], default="com_exemplo")
    ap.add_argument("--variantes", nargs="+", help=f"padrão: todas ({', '.join(VARIANTES)})")
    ap.add_argument("--saida")
    ap.add_argument("--max-iteracoes", type=int, default=5)
    ap.add_argument("--sem-controle", action="store_true",
                    help="não dá os pontos de controle ao agente")
    ap.add_argument("--historico", choices=["ultima", "completo"], default="ultima")
    ap.add_argument("--minutos", type=int, default=2880, help="tamanho do dado sintético")
    ap.add_argument("--temperatura", type=float, default=None)
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--4bit", dest="quatro_bits", action="store_true")
    a = ap.parse_args()
    r = rodar(a.llm, a.modo, a.variantes, a.saida, a.max_iteracoes, not a.sem_controle,
              a.historico, n_minutos=a.minutos, temperatura=a.temperatura,
              max_tokens=a.max_tokens, quantizar_4bit=a.quatro_bits)
    print()
    print(_markdown(r))
    print(f"Detalhes: {r['pasta']}")
    sys.exit(0 if r["corretas"] == r["variantes"] else 1)


if __name__ == "__main__":
    main()
