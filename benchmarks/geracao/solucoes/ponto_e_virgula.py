"""Solução de referência: separador ';', ponto decimal, cabeçalho com nomes."""
import pandas as pd

from geracao_netcdf.aparelhos.rd80 import calcular, dimensoes  # noqa: F401


def ler(arquivos, const):
    partes = []
    for a in arquivos:
        df = pd.read_csv(a, sep=";")
        tempo = pd.to_datetime(df.pop("Date") + " " + df.pop("Time"),
                               format="%Y-%m-%d %H:%M:%S")
        df.index = pd.DatetimeIndex(tempo, name="time")
        partes.append(df)
    return pd.concat(partes)
