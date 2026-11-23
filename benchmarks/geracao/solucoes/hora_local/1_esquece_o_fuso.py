"""Tentativa 1 (de propósito errada): reaproveita o leitor do rd80 e
esquece que o relógio estava em hora local. Os pontos de controle pegam."""
from geracao_netcdf.aparelhos.rd80 import calcular, dimensoes, ler  # noqa: F401
