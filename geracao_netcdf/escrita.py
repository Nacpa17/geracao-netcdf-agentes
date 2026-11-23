"""Grava o NetCDF de um dia seguindo o esquema, com metadados CF/ARM limpos.

Diferenças em relação ao RD80-Processing (ver docs/auditoria no projeto):
- só atributos CF/ARM vão para o arquivo (campos de catálogo ficam de fora);
- missing_value, valid_min, valid_max e flag_masks têm o tipo da variável
  (valores como "N/A" são descartados);
- a coordenada de cada dimensão (ex.: drop_avg_class) sai com os atributos
  do esquema; dimensões sem variável no esquema não viram variável;
- ancillary_variables só cita variáveis que existem no arquivo;
- o qc_time é descrito pelos bits que o núcleo realmente calcula;
- "Conventions" com C maiúsculo; command_line, history e input_source
  refletem a execução real;
- NaN vira missing_value em todas as variáveis (criando um novo array).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np
import pandas as pd

from geracao_netcdf.esquema import ATRIBUTOS_TIPADOS, AUTO, Esquema
from geracao_netcdf.grade import QC_TIME_BITS

ORDEM_TEMPO = ["base_time", "time_offset", "time", "time_bounds"]
GLOBAIS_SEMPRE_DINAMICOS = {"command_line", "process_version", "history",
                            "input_source"}
# texto exigido pela ARM (Data File Standards 1.3, seção 6.8.2) nas variáveis
# de QC com bits
ARM_QC_DESCRICAO = ("This variable contains bit packed integer values, where each bit "
                    "represents a QC test on the data. Non-zero bits indicate the QC "
                    "condition given in the description for those bits; a value of 0 "
                    "(no bits set) indicates the data has not failed any QC tests.")
# tempo e coordenadas não podem ter valor ausente (ARM 1.3, 6.1.1 e 6.2; CF 5)
SEM_AUSENTE = ("missing_value", "_FillValue")


class ErroEsquema(ValueError):
    """O que foi calculado não bate com o esquema (mensagem para humanos e
    para o agente)."""


def _converter_tipo(valor: Any, tipo: str):
    """Converte um atributo tipado (escalar ou lista) para o tipo da variável.
    Devolve None se não der (ex.: "N/A" numa variável inteira)."""
    if tipo == "str":
        return None
    try:
        arr = np.asarray(valor, dtype=float)
        if not np.all(np.isfinite(arr)):
            return None
        out = arr.astype(tipo)
        # escalar numpy (não .item()): float32 continua float32 no arquivo
        return out[()] if out.ndim == 0 else out
    except (TypeError, ValueError):
        return None


def _atributo_simples(valor: Any) -> Any:
    if isinstance(valor, (bool, np.bool_)):
        return int(valor)
    if isinstance(valor, dict):
        return json.dumps(valor, ensure_ascii=False)
    if isinstance(valor, (list, tuple)):
        if all(isinstance(x, (int, float, np.number)) for x in valor):
            return np.asarray(valor)
        return " ".join(str(x) for x in valor)
    return valor


def _dados(valor: Any, tipo: str, missing: Any, nome: str) -> np.ndarray:
    if tipo == "str":
        arr = np.asarray(valor, dtype=object)
        return np.where(pd.isna(arr), "", arr).astype(str)
    arr = np.asarray(valor)
    if arr.dtype == bool:
        arr = arr.astype(np.int64)
    if arr.dtype.kind in "OUS":
        try:
            arr = arr.astype(float)
        except ValueError as e:
            raise ErroEsquema(f"variável '{nome}': valores não numéricos ({e})") from e
    alvo = np.dtype(tipo)
    if arr.dtype.kind == "f" or alvo.kind == "f":
        arr = arr.astype(float)
        invalido = ~np.isfinite(arr)
        if invalido.any():
            if missing is None:
                if alvo.kind != "f":
                    raise ErroEsquema(
                        f"variável '{nome}' ({tipo}) tem valores ausentes e o "
                        f"esquema não define missing_value")
            else:
                arr = np.where(invalido, float(missing), arr)
    return arr.astype(alvo)


def _atributos_variavel(nome: str, var, esquema: Esquema, dia: pd.Timestamp,
                        passo_s: int, escritas: set[str]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for k, v in var.atributos.items():
        if v == AUTO:
            if k in ("delta_t_lower_limit", "delta_t_upper_limit"):
                attrs[k] = float(passo_s)
            continue
        if k in ATRIBUTOS_TIPADOS:
            conv = _converter_tipo(v, var.tipo)
            if conv is not None:
                attrs[k] = conv
            continue
        if k in ("ancillary_variables", "bounds"):
            citadas = [x for x in str(v).split() if x in escritas and x != nome]
            if citadas:
                attrs[k] = " ".join(citadas)
            continue
        attrs[k] = _atributo_simples(v)
    unidade_dia = f"seconds since {dia:%Y-%m-%d} 00:00:00 0:00"
    if nome in ("time", "time_offset"):
        attrs["units"] = unidade_dia
    elif isinstance(attrs.get("units"), str) and "YYYY-MM-DD" in attrs["units"]:
        attrs["units"] = attrs["units"].replace("YYYY-MM-DD", f"{dia:%Y-%m-%d}")
    if nome in ("base_time", "time_offset", "time", "time_bounds") or var.dims == (nome,):
        for k in SEM_AUSENTE:
            attrs.pop(k, None)
    if nome == "base_time":
        attrs["string"] = f"{dia:%Y-%m-%d} 00:00:00 0:00"
        if "time_offset" in escritas:
            attrs.setdefault("ancillary_variables", "time_offset")
    if nome == "time_bounds":
        attrs.pop("units", None)     # herda as de time (CF 7.1; ARM 1.3, 6.1.4)
    if nome == "qc_time":
        # o núcleo calcula o qc_time: a descrição tem que ser a dele, no
        # formato de QC da ARM (Data File Standards 1.3, seção 6.8)
        attrs = {k: v for k, v in attrs.items()
                 if not (k.startswith("bit_") or k.startswith("flag_")
                         or k in ("description", "comment", "valid_min", "valid_max",
                                  "previous_observation_scope", "long_name", "units",
                                  "standard_name"))}
        attrs["long_name"] = "Quality check results on variable: Time offset from midnight"
        attrs["units"] = "1"
        attrs["description"] = ARM_QC_DESCRICAO
        attrs["flag_method"] = "bit"
        attrs["standard_name"] = "quality_flag"
        attrs["comment"] = ("Tests use the original observation start times of all loaded "
                            "input files, before the nominal grid; the first observation "
                            "has no previous record.")
        for i, (mask, nome_bit, desc, aval) in enumerate(QC_TIME_BITS, 1):
            attrs[f"bit_{i}_description"] = desc
            attrs[f"bit_{i}_assessment"] = aval
        attrs["flag_masks"] = np.array([b[0] for b in QC_TIME_BITS], dtype=var.tipo)
        attrs["flag_meanings"] = " ".join(b[1] for b in QC_TIME_BITS)
        attrs["delta_t_lower_limit"] = float(passo_s)
        attrs["delta_t_upper_limit"] = float(passo_s)
    return attrs


def _globais(esquema: Esquema, dinamicos: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in esquema.globais.items():
        chave = "Conventions" if k.lower() == "conventions" else k
        if k in GLOBAIS_SEMPRE_DINAMICOS:
            continue
        if v is None:
            if dinamicos.get(k) is not None:
                out[chave] = dinamicos[k]
            continue
        out.setdefault(chave, _atributo_simples(v))
    for k in sorted(GLOBAIS_SEMPRE_DINAMICOS | {"input_sha256"}):
        if dinamicos.get(k) is not None:
            out[k] = dinamicos[k]
    if "history" in out:                 # a ARM recomenda history por último (1.3, 6.7.1)
        out["history"] = out.pop("history")
    return out


def escrever_netcdf(caminho: str | Path, esquema: Esquema,
                    dims: dict[str, Any], variaveis: dict[str, Any],
                    dia: pd.Timestamp, passo_s: int,
                    globais_dinamicos: dict[str, Any] | None = None,
                    formato: str = "NETCDF4") -> dict[str, Any]:
    """Grava o arquivo (de forma atômica) e devolve um pequeno relatório.

    `dims`: nome -> valores da coordenada (array) ou tamanho (int).
    `variaveis`: nome -> dados; variáveis com valor fixo no esquema (ex.: lat)
    não precisam ser passadas.
    """
    caminho = Path(caminho)
    tamanhos = {k: (int(v) if np.ndim(v) == 0 else len(v)) for k, v in dims.items()}
    dados_finais: dict[str, Any] = {}
    faltando = []
    for nome in esquema.obrigatorias:
        var = esquema.variaveis[nome]
        if nome in variaveis:
            dados_finais[nome] = variaveis[nome]
        elif nome in dims and np.ndim(dims[nome]) == 1:
            dados_finais[nome] = dims[nome]
        elif var.valor is not None:
            dados_finais[nome] = var.valor
        else:
            faltando.append(nome)
    if faltando:
        raise ErroEsquema("faltam variáveis obrigatórias do esquema: "
                          + ", ".join(sorted(faltando)))
    desconhecidas = sorted(set(variaveis) - set(esquema.variaveis))
    proibidas = sorted(set(variaveis) & set(esquema.proibidas))

    convertidos: dict[str, np.ndarray] = {}
    for nome, valor in dados_finais.items():
        var = esquema.variaveis[nome]
        for d in var.dims:
            if d not in tamanhos:
                raise ErroEsquema(f"variável '{nome}' usa a dimensão '{d}', "
                                  f"que `dimensoes`/grade não definiu")
        mv = _converter_tipo(var.missing_value, "float64") if var.tipo != "str" else None
        arr = _dados(valor, var.tipo, None if mv is None else float(mv), nome)
        esperado = tuple(tamanhos[d] for d in var.dims)
        if arr.shape != esperado:
            if arr.size == 1 and not var.dims:
                arr = arr.reshape(())
            else:
                raise ErroEsquema(f"variável '{nome}' tem forma {arr.shape}, o "
                                  f"esquema pede {var.dims} = {esperado}")
        convertidos[nome] = arr

    ordem = [n for n in ORDEM_TEMPO if n in convertidos] + \
            [n for n in esquema.variaveis if n in convertidos and n not in ORDEM_TEMPO]
    escritas = set(ordem)
    globais = _globais(esquema, globais_dinamicos or {})

    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=caminho.name + ".", suffix=".tmp",
                               dir=caminho.parent)
    os.close(fd)
    try:
        with netCDF4.Dataset(tmp, "w", format=formato) as nc:
            usadas = {d for n in ordem for d in esquema.variaveis[n].dims}
            for d in [x for x in tamanhos if x in usadas]:
                nc.createDimension(d, None if d == "time" else tamanhos[d])
            comprimir = formato.startswith("NETCDF4")
            for nome in ordem:
                var = esquema.variaveis[nome]
                arr = convertidos[nome]
                if var.tipo == "str":
                    v = nc.createVariable(nome, str, var.dims)
                    v[:] = arr.astype(object) if arr.ndim else np.array([str(arr)], dtype=object)[0]
                else:
                    v = nc.createVariable(nome, np.dtype(var.tipo), var.dims,
                                          zlib=comprimir, complevel=4,
                                          fill_value=False)
                    v[...] = arr
                attrs = _atributos_variavel(nome, var, esquema, dia, passo_s, escritas)
                for k, val in attrs.items():
                    v.setncattr(k, val)
            for k, val in globais.items():
                nc.setncattr(k, val)
        # mkstemp cria com 0600; o arquivo final segue a umask (ex.: 0644)
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, caminho)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return {"arquivo": str(caminho), "variaveis": len(ordem),
            "ignoradas_fora_do_esquema": desconhecidas,
            "ignoradas_opcionais": proibidas}
