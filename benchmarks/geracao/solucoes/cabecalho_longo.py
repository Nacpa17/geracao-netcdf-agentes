"""Solução de referência: comentários '#', linha de nomes + linha de unidades, dd/mm/aaaa HH:MM."""
import io

import pandas as pd

from geracao_netcdf.aparelhos.rd80 import calcular, dimensoes  # noqa: F401


def ler(arquivos, const):
    partes = []
    for a in arquivos:
        with open(a, encoding="utf-8") as f:
            linhas = [x for x in f.read().splitlines() if not x.startswith("#")]
        texto = "\n".join([linhas[0]] + linhas[2:])          # sem a linha de unidades
        df = pd.read_csv(io.StringIO(texto)).rename(columns={"status": "Status",
                                                             "intervalo": "Interval"})
        tempo = pd.to_datetime(df.pop("data") + " " + df.pop("hora"), format="%d/%m/%Y %H:%M")
        df.index = pd.DatetimeIndex(tempo, name="time")
        partes.append(df)
    return pd.concat(partes)
