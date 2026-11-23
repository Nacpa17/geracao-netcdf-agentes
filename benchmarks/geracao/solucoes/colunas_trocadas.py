"""Solução de referência: DISDRODATA com as colunas em outra ordem (lê pelo cabeçalho)."""
import pandas as pd

from geracao_netcdf.aparelhos.rd80 import calcular, dimensoes  # noqa: F401

NOMES = {"YYYY-MM-DD": "Date", "hh:mm:ss": "Time", "Interval [s]": "Interval",
         "RI [mm/h]": "RI", "RA [mm]": "RA", "RAT [mm]": "RAT"}


def ler(arquivos, const):
    partes = []
    for a in arquivos:
        df = pd.read_csv(a, sep="\t", decimal=",").rename(columns=NOMES)
        tempo = pd.to_datetime(df.pop("Date") + " " + df.pop("Time"),
                               format="%Y-%m-%d %H:%M:%S")
        df.index = pd.DatetimeIndex(tempo, name="time")
        partes.append(df)
    return pd.concat(partes)
