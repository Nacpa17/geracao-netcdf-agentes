"""Esquema de metadados no formato do ATTO.

Dois arquivos por aparelho:

- `netCDF_info.json`: atributos globais, variáveis (tipo, dimensões,
  long_name, units, missing_value, ...) e atributos extras por variável
  (`variable_specific_attrs`, ex.: descrição dos bits de QC);
- `variables_info.json`: constantes do aparelho (área do sensor, classes,
  tempo de integração, colunas do arquivo bruto, ...).

Convenções herdadas do RD80-Processing:
- "Optional": false -> variável obrigatória; true -> NÃO deve ser gravada;
- "value": "__AUTO__" (ou null) -> o código preenche; outro valor -> fixo.

Campos de catálogo (Name, Variable ID, Shortname, Description,
Classification, Multivalue, Parent ID) servem ao banco de metadados do
ATTO e não são gravados no NetCDF.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AUTO = "__AUTO__"
CAMPOS_CATALOGO = {"Name", "Variable ID", "Shortname", "Description",
                   "Classification", "Multivalue", "Parent ID"}
CAMPOS_ESQUEMA = {"Datatype", "Optional", "value", "dimensions"}
# atributos com o MESMO tipo da variável (CF): convertidos na escrita
ATRIBUTOS_TIPADOS = {"missing_value", "_FillValue", "valid_min", "valid_max",
                     "valid_range", "flag_masks", "flag_values"}
# ("none" NÃO entra: a ARM usa units="none" de verdade)
VAZIOS = {None, "", AUTO, "TBD", "N/A", "n/a"}


def vazio(valor: Any) -> bool:
    """True para valores que significam "não informado" no esquema."""
    try:
        return valor in VAZIOS
    except TypeError:          # listas/dicts não são "hashable"
        return False


@dataclass
class Variavel:
    nome: str
    tipo: str                         # "float32", "int64", "str", ...
    dims: tuple[str, ...]
    atributos: dict[str, Any]         # só atributos CF/ARM (sem catálogo)
    valor: Any = None                 # valor fixo do esquema (ex.: lat)
    opcional: bool = False
    id_catalogo: str | None = None

    @property
    def missing_value(self) -> Any:
        mv = self.atributos.get("missing_value")
        return None if vazio(mv) else mv


@dataclass
class Esquema:
    variaveis: dict[str, Variavel]
    globais: dict[str, Any]           # nome -> valor (None = preencher)
    globais_opcionais: set[str] = field(default_factory=set)
    origem: str | None = None

    @property
    def obrigatorias(self) -> list[str]:
        return [n for n, v in self.variaveis.items() if not v.opcional]

    @property
    def proibidas(self) -> list[str]:
        return [n for n, v in self.variaveis.items() if v.opcional]

    @property
    def datastream(self) -> str | None:
        return self.globais.get("datastream")


def _tipo(datatype: Any) -> str:
    t = str(datatype or "float32").strip().lower()
    return {"string": "str", "char": "str", "text": "str", "double": "float64",
            "float": "float32", "int": "int32", "integer": "int32",
            "long": "int64", "short": "int16", "byte": "int8"}.get(t, t)


def _atributos(bloco: dict[str, Any], extras: dict[str, Any]) -> dict[str, Any]:
    attrs = {}
    for k, v in bloco.items():
        if k in CAMPOS_CATALOGO or k in CAMPOS_ESQUEMA:
            continue
        if vazio(v):
            continue
        attrs[k] = v
    for k, v in (extras or {}).items():
        if not vazio(v):
            attrs[k] = v
        elif v == AUTO:
            attrs[k] = AUTO        # o código precisa preencher (ex.: limites)
    return attrs


def carregar_esquema(caminho: str | Path) -> Esquema:
    caminho = Path(caminho)
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    extras = dados.get("variable_specific_attrs") or {}
    variaveis: dict[str, Variavel] = {}
    for nome, bloco in (dados.get("variables") or {}).items():
        dims = bloco.get("dimensions") or []
        if isinstance(dims, str):
            dims = [dims]
        valor = bloco.get("value")
        vid = bloco.get("Variable ID")
        variaveis[nome] = Variavel(
            nome=nome, tipo=_tipo(bloco.get("Datatype")), dims=tuple(dims),
            atributos=_atributos(bloco, extras.get(nome, {})),
            valor=None if vazio(valor) else valor,
            opcional=bool(bloco.get("Optional", False)),
            id_catalogo=str(vid) if vid is not None and str(vid).isdigit() else None)
    globais: dict[str, Any] = {}
    opcionais: set[str] = set()
    for nome, bloco in (dados.get("global attributes") or {}).items():
        if isinstance(bloco, dict):
            valor = bloco.get("value")
            if bloco.get("Optional", False):
                opcionais.add(nome)
        else:                      # formato simples: {"nome": "valor"}
            valor = bloco
        globais[nome] = None if vazio(valor) else valor
    return Esquema(variaveis=variaveis, globais=globais,
                   globais_opcionais=opcionais, origem=str(caminho))


def carregar_constantes(caminho: str | Path) -> dict[str, Any]:
    """variables_info.json: constantes do aparelho (dict simples)."""
    return json.loads(Path(caminho).read_text(encoding="utf-8"))
