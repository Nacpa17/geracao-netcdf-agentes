"""Tentativa 2: mesmo leitor, convertendo hora local de Manaus (UTC-4) para UTC."""
import pandas as pd

from geracao_netcdf.aparelhos.rd80 import calcular, dimensoes  # noqa: F401
from geracao_netcdf.aparelhos.rd80 import ler as ler_disdrodata


def ler(arquivos, const):
    df = ler_disdrodata(arquivos, const)
    df.index = df.index + pd.Timedelta(hours=4)      # UTC = local + 4 h
    return df
