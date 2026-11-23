"""Dados sintéticos de RD-80 e variações de formato (benchmark e testes).

O mesmo conjunto de minutos — chuva simulada (Marshall-Palmer + sorteio de
Poisson por classe), com garoa, uma lacuna e um registro repetido, como nos
dados reais — é escrito em vários formatos de arquivo que aparecem na
prática: outro datalogger, outro separador, fuso local, horário de fim do
intervalo, cabeçalho comprido. Um plug-in correto para QUALQUER formato tem
de gerar NetCDFs idênticos ao gabarito (o formato original lido pelo rd80).

Não usa dados reais do ATTO: pode ir para o repositório e para o CI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from geracao_netcdf.aparelhos import caminho_do_aparelho
from geracao_netcdf.esquema import carregar_constantes

PASTA_RD80 = caminho_do_aparelho("rd80")
CONTAGENS = [f"n{i}" for i in range(1, 21)]


def constantes_rd80() -> dict:
    return carregar_constantes(PASTA_RD80 / "variables_info.json")


def simular_minutos(inicio: str = "2024-12-15 13:53:00", n_minutos: int = 2880,
                    semente: int = 7, const: dict | None = None) -> pd.DataFrame:
    """Um registro por minuto (índice = início do minuto, UTC), com as
    colunas canônicas do RD-80: Status, Interval, n1..n20, RI, RA, RAT."""
    const = const or constantes_rd80()
    D = np.asarray(const["drop_mean_diam"], dtype=float)
    v = np.asarray(const["fall_vell"], dtype=float)
    dD = np.asarray(const["delta_diam"], dtype=float)
    A, dt = float(const["sensor_area"]), 60.0
    rng = np.random.default_rng(semente)
    tempos = pd.date_range(inicio, periods=n_minutos, freq="60s")
    frac = np.arange(n_minutos) / n_minutos

    taxa = np.zeros(n_minutos)                     # chuva "verdadeira" (mm/h)
    for a, b, pico in [(0.06, 0.11, 6.0), (0.30, 0.42, 38.0), (0.55, 0.57, 1.2),
                       (0.71, 0.80, 75.0), (0.90, 0.93, 3.0)]:
        dentro = (frac >= a) & (frac < b)
        x = (frac[dentro] - a) / (b - a)
        taxa[dentro] = 0.05 + pico * np.sin(np.pi * x) ** 2
    lam = 4.1 * np.power(np.where(taxa > 0, taxa, 1.0), -0.21)
    esperado = 8000.0 * np.exp(-lam[:, None] * D) * v * A * dt * dD
    n = np.where(taxa[:, None] > 0, rng.poisson(esperado), 0)
    garoa = (taxa == 0) & (rng.random(n_minutos) < 0.03)       # poucas gotas (bit 2)
    n[garoa, :5] = rng.poisson([2.0, 1.5, 1.0, 0.5, 0.2], size=(int(garoa.sum()), 5))

    ri = (np.pi / 6.0) * (3.6 / 10.0**3) / (A * dt) * (n * D**3).sum(axis=1)
    ri = np.round(ri, 4)
    ra = np.round(ri * dt / 3600.0, 4)
    df = pd.DataFrame(n, index=pd.DatetimeIndex(tempos, name="time"), columns=CONTAGENS)
    df.insert(0, "Interval", 60)
    df.insert(0, "Status", "")
    df["RI"], df["RA"] = ri, ra
    df["RAT"] = np.round(15.3592 + np.cumsum(ra), 4)

    # imperfeições dos dados reais: lacuna de 9 min e um registro repetido
    i_lac = int(0.47 * n_minutos)
    i_rep = int(0.33 * n_minutos)
    manter = np.ones(n_minutos, dtype=bool)
    manter[i_lac:i_lac + 9] = False
    partes = [df.iloc[:i_rep + 1], df.iloc[i_rep:i_rep + 1], df.iloc[i_rep + 1:]]
    df = pd.concat(partes)
    manter = np.insert(manter, i_rep + 1, True)
    return df[manter]


# --------------------------------------------------------------------------
# formatos
# --------------------------------------------------------------------------

def _num(x: float, casas: int, virgula: bool) -> str:
    s = f"{x:.{casas}f}"
    return s.replace(".", ",") if virgula else s


def _blocos(df: pd.DataFrame, n: int):
    for i in range(0, len(df), n):
        yield df.iloc[i:i + n]


def _gravar(caminho: Path, linhas: list[str], fim: str = "\n") -> Path:
    if caminho.exists():            # dois blocos com o mesmo nome: um apagaria o outro
        raise FileExistsError(caminho)
    caminho.write_bytes((fim.join(linhas) + fim).encode("utf-8"))
    return caminho


def _contagens(linha) -> list[str]:
    return [str(int(linha[c])) for c in CONTAGENS]


def _original(df, pasta, n, deslocamento=pd.Timedelta(0), nome_arq="RD-{t:%y%m%d-%H%M%S}.txt"):
    cab = "\t".join(["YYYY-MM-DD", "hh:mm:ss", "Status", "Interval [s]", *CONTAGENS,
                     "RI [mm/h]", "RA [mm]", "RAT [mm]"])
    arquivos = []
    for bloco in _blocos(df, n):
        linhas = [cab]
        for t, r in bloco.iterrows():
            t = t + deslocamento
            linhas.append("\t".join([f"{t:%Y-%m-%d}", f"{t:%H:%M:%S}", r["Status"],
                                     str(int(r["Interval"])), *_contagens(r),
                                     *(_num(r[c], 4, True) for c in ("RI", "RA", "RAT"))]))
        t0 = bloco.index[0] + deslocamento
        arquivos.append(_gravar(pasta / nome_arq.format(t=t0), linhas, "\r\n"))
    return arquivos


def _hora_local(df, pasta, n):
    return _original(df, pasta, n, deslocamento=pd.Timedelta(hours=-4))


def _ponto_e_virgula(df, pasta, n):
    cab = ";".join(["Date", "Time", "Status", "Interval", *CONTAGENS, "RI", "RA", "RAT"])
    arquivos = []
    for bloco in _blocos(df, n):
        linhas = [cab] + [";".join([f"{t:%Y-%m-%d}", f"{t:%H:%M:%S}", r["Status"],
                                    str(int(r["Interval"])), *_contagens(r),
                                    *(_num(r[c], 4, False) for c in ("RI", "RA", "RAT"))])
                          for t, r in bloco.iterrows()]
        arquivos.append(_gravar(pasta / f"rd80_{bloco.index[0]:%Y%m%d_%H%M}.dat", linhas))
    return arquivos


def _iso_utc(df, pasta, n):
    nomes = [f"n{i:02d}" for i in range(1, 21)]
    cab = ",".join(["timestamp", "interval_s", *nomes, "ri_mm_h", "ra_mm", "rat_mm", "status"])
    arquivos = []
    for bloco in _blocos(df, n):
        linhas = [cab] + [",".join([f"{t:%Y-%m-%dT%H:%M:%SZ}", str(int(r["Interval"])),
                                    *_contagens(r),
                                    *(_num(r[c], 4, False) for c in ("RI", "RA", "RAT")),
                                    r["Status"]])
                          for t, r in bloco.iterrows()]
        arquivos.append(_gravar(pasta / f"disdro_{bloco.index[0]:%Y%m%dT%H%M}Z.csv", linhas))
    return arquivos


def _fim_do_intervalo(df, pasta, n):
    cab = "\t".join(["date", "time_end", "interval", *CONTAGENS, "ri", "ra", "rat"])
    arquivos = []
    for bloco in _blocos(df, n):
        linhas = [cab]
        for t, r in bloco.iterrows():
            fim = t + pd.Timedelta(seconds=int(r["Interval"]))
            linhas.append("\t".join([f"{fim:%Y-%m-%d}", f"{fim:%H:%M:%S}",
                                     str(int(r["Interval"])), *_contagens(r),
                                     *(_num(r[c], 4, False) for c in ("RI", "RA", "RAT"))]))
        arquivos.append(_gravar(pasta / f"RD80_{bloco.index[0]:%Y%m%d%H%M}.tsv", linhas))
    return arquivos


def _colunas_trocadas(df, pasta, n):
    cab = "\t".join(["YYYY-MM-DD", "hh:mm:ss", "RI [mm/h]", "RA [mm]", "RAT [mm]",
                     "Interval [s]", "Status", *CONTAGENS])
    arquivos = []
    for bloco in _blocos(df, n):
        linhas = [cab] + ["\t".join([f"{t:%Y-%m-%d}", f"{t:%H:%M:%S}",
                                     *(_num(r[c], 4, True) for c in ("RI", "RA", "RAT")),
                                     str(int(r["Interval"])), r["Status"], *_contagens(r)])
                          for t, r in bloco.iterrows()]
        arquivos.append(_gravar(pasta / f"RD-{bloco.index[0]:%y%m%d-%H%M%S}.txt", linhas, "\r\n"))
    return arquivos


def _cabecalho_longo(df, pasta, n):
    nomes = ["data", "hora", "status", "intervalo", *CONTAGENS, "RI", "RA", "RAT"]
    unidades = ["", "", "", "s", *[""] * 20, "mm/h", "mm", "mm"]
    arquivos = []
    for bloco in _blocos(df, n):
        linhas = ["# Estacao: ATTO - torre de 80 m (sintetico)",
                  "# Instrumento: RD-80 Joss-Waldvogel, s/n 0000",
                  "# Fuso horario: UTC",
                  "# Horario: inicio do intervalo de integracao",
                  "# Separador: virgula; decimal: ponto",
                  "# Gerado por: logger-torre v2.3",
                  ",".join(nomes), ",".join(unidades)]
        linhas += [",".join([f"{t:%d/%m/%Y}", f"{t:%H:%M}", r["Status"], str(int(r["Interval"])),
                             *_contagens(r), *(_num(r[c], 4, False) for c in ("RI", "RA", "RAT"))])
                   for t, r in bloco.iterrows()]
        arquivos.append(_gravar(pasta / f"torre_rd80_{bloco.index[0]:%Y%m%d_%H%M}.csv", linhas))
    return arquivos


@dataclass
class Variante:
    nome: str
    descricao: str          # o que a pessoa diria ao agente sobre o formato
    extensao: str
    escrever: Callable[[pd.DataFrame, Path, int], list[Path]]


VARIANTES: dict[str, Variante] = {v.nome: v for v in [
    Variante("original",
             "Arquivos DISDRODATA do RD-80 como saem do software da Distromet: separados "
             "por TAB, vírgula decimal, uma linha de cabeçalho, uma linha por minuto, "
             "horário UTC do INÍCIO do minuto.", ".txt", _original),
    Variante("ponto_e_virgula",
             "Exportação do datalogger: separador ';', ponto decimal, cabeçalho com os "
             "nomes das colunas, horário UTC do início do minuto.", ".dat", _ponto_e_virgula),
    Variante("iso_utc",
             "CSV (vírgula como separador, ponto decimal) com uma coluna 'timestamp' em "
             "ISO 8601 terminada em 'Z' (UTC, início do minuto); as contagens das classes "
             "se chamam n01..n20 e o intervalo de integração é 'interval_s'.", ".csv", _iso_utc),
    Variante("hora_local",
             "Mesmo formato DISDRODATA original (TAB, vírgula decimal, início do minuto), "
             "mas o relógio do computador de aquisição estava em hora local de Manaus "
             "(America/Manaus, UTC-4, sem horário de verão). Converta para UTC.",
             ".txt", _hora_local),
    Variante("fim_do_intervalo",
             "Datalogger novo: separado por TAB, ponto decimal, sem coluna de status; o "
             "horário (UTC) marca o FIM do intervalo de integração: o registro de "
             "14:01:00 cobre 14:00:00-14:01:00.", ".tsv", _fim_do_intervalo),
    Variante("colunas_trocadas",
             "DISDRODATA de uma versão antiga do software: TAB, vírgula decimal, horário UTC "
             "do início do minuto, mas com as colunas em outra ordem (veja o cabeçalho).",
             ".txt", _colunas_trocadas),
    Variante("cabecalho_longo",
             "Exportação do sistema da torre: 6 linhas de comentário iniciadas por '#', "
             "depois uma linha com os nomes das colunas e outra com as unidades; separador "
             "vírgula, ponto decimal; data dd/mm/aaaa e hora HH:MM (UTC, início do minuto).",
             ".csv", _cabecalho_longo),
]}


def escrever_variante(nome: str, df: pd.DataFrame, pasta: str | Path,
                      minutos_por_arquivo: int = 1440) -> list[Path]:
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    return VARIANTES[nome].escrever(df, pasta, minutos_por_arquivo)


def constantes_da_variante(nome: str, destino: str | Path) -> Path:
    """Constantes do RD-80 com a extensão de arquivo do formato."""
    const = constantes_rd80()
    const["input_file_extension"] = VARIANTES[nome].extensao
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(const, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino
