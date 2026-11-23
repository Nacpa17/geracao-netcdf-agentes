"""Solução de referência: horário no FIM do intervalo -> início = fim - intervalo."""
import pandas as pd

from geracao_netcdf.aparelhos.rd80 import calcular, dimensoes  # noqa: F401

NOMES = {"interval": "Interval", "ri": "RI", "ra": "RA", "rat": "RAT"}


def ler(arquivos, const):
    partes = []
    for a in arquivos:
        df = pd.read_csv(a, sep="\t").rename(columns=NOMES)
        fim = pd.to_datetime(df.pop("date") + " " + df.pop("time_end"),
                             format="%Y-%m-%d %H:%M:%S")
        inicio = fim - pd.to_timedelta(df["Interval"], unit="s")
        df.index = pd.DatetimeIndex(inicio, name="time")
        partes.append(df)
    return pd.concat(partes)
