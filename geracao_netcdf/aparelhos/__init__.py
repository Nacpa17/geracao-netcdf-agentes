"""Plug-ins de aparelhos: a única parte que muda de um aparelho para outro.

Um plug-in é um módulo Python com TRÊS funções (contrato abaixo). Pode ser
um pacote daqui (`geracao_netcdf/aparelhos/<nome>/__init__.py`, com o esquema
`netCDF_info.json` e as constantes `variables_info.json` na mesma pasta) ou
um arquivo .py avulso — é o que o agente escreve.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from types import ModuleType

PASTA = Path(__file__).resolve().parent

CONTRATO = '''
def ler(arquivos: list[pathlib.Path], const: dict) -> pandas.DataFrame:
    """Lê os arquivos brutos do aparelho.
    - Índice: horário de INÍCIO de cada observação, em UTC (DatetimeIndex sem
      fuso; se o arquivo estiver em hora local, converta para UTC).
    - Colunas: o que o aparelho grava (contagens, intervalo, status...),
      numéricas como float (vírgula decimal -> ponto).
    - Não ordene nem remova linhas repetidas: o núcleo faz isso e marca no
      qc_time."""

def dimensoes(const: dict) -> dict[str, numpy.ndarray]:
    """Dimensões além de `time`, com os valores da coordenada
    (ex.: {"drop_avg_class": diâmetros médios das classes}). Pode ser {}."""

def calcular(grade, const: dict) -> dict[str, numpy.ndarray]:
    """Variáveis do esquema para UM dia.
    `grade.dados`: DataFrame com uma linha por passo nominal do dia (as
    colunas do `ler`; NaN onde não houve observação). Também: grade.presente
    (bool), grade.tempos (horários UTC), grade.passo_s, grade.qc_time,
    grade.segundos (segundos desde 00:00).
    Devolva {nome_da_variavel: array} com a forma das dimensões do esquema
    (time -> len(grade)). Use NaN para valor ausente (o núcleo troca pelo
    missing_value). O núcleo já cria base_time, time_offset, time e qc_time;
    valores fixos do esquema (ex.: lat, lon, alt) não precisam ser devolvidos."""
'''


def caminho_do_aparelho(nome: str) -> Path:
    return PASTA / nome


def listar() -> list[str]:
    return sorted(p.name for p in PASTA.iterdir()
                  if p.is_dir() and (p / "__init__.py").exists())


def carregar(ref: str) -> ModuleType:
    """`ref`: nome de um aparelho daqui (ex.: "rd80") ou caminho de um .py."""
    p = Path(ref)
    if p.suffix == ".py" and p.exists():
        spec = importlib.util.spec_from_file_location(f"plugin_{p.stem}", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    else:
        mod = importlib.import_module(f"geracao_netcdf.aparelhos.{ref}")
    faltam = [f for f in ("ler", "dimensoes", "calcular") if not callable(getattr(mod, f, None))]
    if faltam:
        raise AttributeError(f"o plug-in {ref} não define: {', '.join(faltam)}")
    return mod
