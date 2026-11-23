"""Solução de referência: CSV com timestamp ISO 8601 (UTC) e outros nomes de coluna."""
import pandas as pd

from geracao_netcdf.aparelhos.rd80 import calcular, dimensoes  # noqa: F401

NOMES = {f"n{i:02d}": f"n{i}" for i in range(1, 21)}
NOMES.update(interval_s="Interval", ri_mm_h="RI", ra_mm="RA", rat_mm="RAT", status="Status")


def ler(arquivos, const):
    partes = []
    for a in arquivos:
        df = pd.read_csv(a).rename(columns=NOMES)
        tempo = pd.to_datetime(df.pop("timestamp"), utc=True).dt.tz_localize(None)
        df.index = pd.DatetimeIndex(tempo, name="time")
        partes.append(df)
    return pd.concat(partes)
