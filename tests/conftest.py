"""Fixtures compartilhadas: dado sintético de RD-80 e os NetCDFs gerados a partir dele."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for p in (ROOT, TESTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# --- geração de NetCDF (geracao_netcdf): dado sintético de RD-80 -------------
# 900 minutos a partir de 13:53 (atravessa a meia-noite), em 2 arquivos.
GERACAO_MINUTOS, GERACAO_POR_ARQUIVO = 900, 450


@pytest.fixture(scope="session")
def rd80_minutos():
    pytest.importorskip("netCDF4")
    from geracao_netcdf.sintetico import simular_minutos
    return simular_minutos(n_minutos=GERACAO_MINUTOS)


@pytest.fixture(scope="session")
def rd80_brutos(rd80_minutos, tmp_path_factory) -> list[Path]:
    """Arquivos no formato DISDRODATA original."""
    from geracao_netcdf.sintetico import escrever_variante
    return escrever_variante("original", rd80_minutos, tmp_path_factory.mktemp("rd80_brutos"),
                             GERACAO_POR_ARQUIVO)


@pytest.fixture(scope="session")
def rd80_netcdf(rd80_brutos, tmp_path_factory) -> list[Path]:
    """NetCDFs gerados pelo plug-in rd80 (não altere: copie antes)."""
    from geracao_netcdf.aparelhos import caminho_do_aparelho, carregar
    from geracao_netcdf.esquema import carregar_constantes, carregar_esquema
    from geracao_netcdf.gerar import gerar
    pasta = caminho_do_aparelho("rd80")
    rel = gerar(carregar("rd80"), rd80_brutos, carregar_esquema(pasta / "netCDF_info.json"),
                carregar_constantes(pasta / "variables_info.json"),
                tmp_path_factory.mktemp("rd80_nc"), linha_de_comando="pytest")
    return [Path(r["arquivo"]) for r in rel]
