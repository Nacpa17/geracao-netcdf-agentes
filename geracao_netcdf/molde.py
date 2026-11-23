"""Molde do esquema de um aparelho novo, tirado dos metadados da ARM.

Fecha o ciclo da IC: os metadados que o projeto aprendeu a consultar (DOD,
arquivos da ARM) viram o esquema que o gerador e o agente usam para
produzir o arquivo novo. Duas fontes:

- um NetCDF de exemplo da ARM (ex.: os de disdrômetro): copia variáveis,
  tipos, dimensões, atributos e globais; as coordenadas que não dependem do
  tempo (diâmetros e velocidades das classes, por exemplo) viram constantes;
- o DOD oficial da ARM (pcm.arm.gov, com cache local em data/arm_extra/cache).

    python -m geracao_netcdf.molde --de-netcdf sgpldE13.b1.20230828.000000.cdf \\
        --saida aparelhos_novos/parsivel2 --datastream attldCA.b1 --site att \\
        --facility CA --lat -2.1815896 --lon -59.0218639 --alt 59
    python -m geracao_netcdf.molde --de-dod ld.b1 --saida aparelhos_novos/parsivel2
    python -m geracao_netcdf.molde --dod-json dod_ld_b1.json --saida aparelhos_novos/parsivel2

Gera `netCDF_info.json`, `variables_info.json` e `tarefa.yaml` (para o
agente). O que é do local/aparelho e precisa de revisão fica listado em
"_revisar" no esquema.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from geracao_netcdf.esquema import ATRIBUTOS_TIPADOS, AUTO

DINAMICOS = {"command_line", "process_version", "input_source", "history",
             "input_sha256"}
REVISAR = ["datastream", "site_id", "facility_id", "platform_id", "location",
           "location_description", "serial_number", "calibration_date", "calib_date",
           "effective_measurement_area", "doi", "dod_version", "title",
           "input_datastreams"]
TIPOS_DOD = {"float": "float32", "double": "float64", "int": "int32", "short": "int16",
             "byte": "int8", "ubyte": "uint8", "int64": "int64", "char": "str", "string": "str"}
UNIDADE_TEMPO = "seconds since YYYY-MM-DD 00:00:00 0:00"


def _json(v: Any) -> Any:
    """Valor de atributo/variável NetCDF -> JSON. float32 sai com a grafia
    curta (0.062, e não 0.06199999898672104)."""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, (np.ndarray, np.generic)):
        arr = np.asarray(v)
        if arr.dtype.kind == "S":
            return b"".join(arr.ravel()).decode("utf-8", "replace").rstrip("\x00 ")
        if arr.dtype == np.float32:
            curto = [float(np.format_float_positional(x, unique=True, trim="-"))
                     if np.isfinite(x) else float(x) for x in arr.ravel()]
            arr = np.asarray(curto, dtype=float).reshape(arr.shape)
        return arr.tolist() if arr.ndim else arr.item()
    return v


def _numero(texto: str) -> Any:
    """'-9999' -> -9999 ; '1, 2, 4' -> [1, 2, 4] (atributos tipados do DOD)."""
    partes = [p for p in re.split(r"[,\s]+", str(texto).strip()) if p]
    try:
        nums = [float(p) for p in partes]
    except ValueError:
        return texto
    nums = [int(x) if x.is_integer() else x for x in nums]
    return nums[0] if len(nums) == 1 else nums


def _cabecalho(fonte: str) -> dict[str, Any]:
    return {
        "_sobre": (f"Molde gerado por geracao_netcdf.molde a partir de {fonte} em "
                   f"{datetime.now(timezone.utc):%Y-%m-%d}. 'Optional': true = não gravar; "
                   "'value': '__AUTO__' = o código preenche."),
        "_revisar": [],
    }


def _ajustar(esq: dict, datastream, site, facility, lat, lon, alt) -> None:
    g, v = esq["global attributes"], esq["variables"]
    for nome, valor in (("datastream", datastream), ("site_id", site), ("facility_id", facility)):
        if valor:
            g.setdefault(nome, {"Optional": False})["value"] = valor
    for nome, valor in (("lat", lat), ("lon", lon), ("alt", alt)):
        if valor is not None and nome in v:
            v[nome]["value"] = float(valor)
    rev = [f"global {k}: {g[k]['value']!r}" for k in REVISAR if k in g
           and not (k == "datastream" and datastream)
           and not (k == "site_id" and site) and not (k == "facility_id" and facility)]
    rev += [f"variável {k}: valor {v[k]['value']!r} do exemplo — use o do seu local"
            for k, dado in (("lat", lat), ("lon", lon), ("alt", alt))
            if k in v and dado is None]
    rev.append("variáveis: marque 'Optional': true nas que o seu aparelho não mede")
    esq["_revisar"] = rev


def molde_de_netcdf(caminho: str | Path) -> tuple[dict, dict]:
    import netCDF4

    caminho = Path(caminho)
    esq = _cabecalho(caminho.name)
    const: dict[str, Any] = {"input_file_extension": "", "integration_time": None}
    with netCDF4.Dataset(caminho) as nc:
        nc.set_auto_mask(False)
        esq["global attributes"] = {
            a: {"value": AUTO if a in DINAMICOS else _json(nc.getncattr(a)), "Optional": False}
            for a in nc.ncattrs()}
        esq["dimensions"] = {d: ("arquivo diário: o núcleo cria" if (x.isunlimited() or d == "time")
                                 else len(x)) for d, x in nc.dimensions.items()}
        variaveis: dict[str, Any] = {}
        for nome, var in nc.variables.items():
            char = var.dtype is not str and np.dtype(var.dtype).kind == "S"
            tipo = "str" if var.dtype is str or char else np.dtype(var.dtype).name
            dims = list(var.dimensions[:-1] if char and var.dimensions else var.dimensions)
            bloco: dict[str, Any] = {"Datatype": tipo, "dimensions": dims,
                                     "Optional": False, "value": AUTO}
            for a in var.ncattrs():
                if a != "_FillValue":
                    bloco[a] = _json(var.getncattr(a))
            if nome in ("time", "time_offset"):
                bloco["units"] = UNIDADE_TEMPO
            if nome == "base_time":
                bloco.pop("string", None)
            if not dims and nome != "base_time" and tipo != "str":
                bloco["value"] = _json(np.asarray(var[...]))
            elif dims and "time" not in dims and tipo != "str":
                const[nome] = _json(np.asarray(var[:]))          # classes, velocidades...
            variaveis[nome] = bloco
        esq["variables"] = variaveis
        if "time" in nc.variables and len(nc["time"]) > 1:
            passo = float(np.median(np.diff(np.asarray(nc["time"][:], dtype=float))))
            const["integration_time"] = int(round(passo))
    return esq, const


def molde_de_dod(bruto: dict, proc: str, versao: str | None = None) -> tuple[dict, dict]:
    from geracao_netcdf.arm_services import _atts

    versoes = (bruto or {}).get("versions") or {}
    if not versoes:
        raise ValueError(f"DOD de {proc} vazio ou inexistente")
    chave = versao or list(versoes)[-1]
    v = versoes[chave]
    esq = _cabecalho(f"DOD {proc} versão {chave}")
    gatts = _atts(v.get("atts"))
    gatts.setdefault("dod_version", f"{proc.replace('.', '-')}-{chave}")
    esq["global attributes"] = {k: {"value": AUTO if k in DINAMICOS else val, "Optional": False}
                                for k, val in gatts.items()}
    for k in sorted(DINAMICOS - set(gatts)):
        esq["global attributes"][k] = {"value": AUTO, "Optional": False}
    esq["dimensions"] = {d["name"]: ("arquivo diário: o núcleo cria" if d["name"] == "time"
                                     else d.get("length")) for d in v.get("dims") or []}
    variaveis: dict[str, Any] = {}
    for var in v.get("vars") or []:
        nome = var.get("name")
        if not nome:
            continue
        tipo = TIPOS_DOD.get(str(var.get("type", "float")).lower(), "float32")
        if any((x or {}).get("name") == "string" for x in var.get("atts") or []):
            tipo = "str"                    # marcador de texto do DOD (regra do ACT)
        dims = list(var.get("dims") or [])
        if tipo == "str" and dims and dims[-1] not in ("time",) and dims[-1] not in esq["dimensions"]:
            dims = dims[:-1]
        bloco: dict[str, Any] = {"Datatype": tipo, "dimensions": dims, "Optional": False,
                                 "value": "TBD" if nome in ("lat", "lon", "alt") else AUTO}
        for k, val in _atts(var.get("atts")).items():
            bloco[k] = _numero(val) if k in ATRIBUTOS_TIPADOS and isinstance(val, str) else val
        if nome in ("time", "time_offset"):
            bloco["units"] = UNIDADE_TEMPO
        variaveis[nome] = bloco
    esq["variables"] = variaveis
    return esq, {"input_file_extension": "", "integration_time": None}


TAREFA = """\
# Tarefa para o agente (python -m geracao_netcdf.agente --tarefa {pasta}/tarefa.yaml --llm ...)
nome: {nome}
descricao: |
  TODO: descreva o aparelho e o FORMATO do arquivo bruto: separador, decimal,
  cabeçalho, fuso horário, se o horário é do início ou do fim da integração,
  e o que significa cada coluna.
amostras: amostras/            # TODO: ponha 1-3 arquivos brutos de exemplo aqui
esquema: netCDF_info.json      # revise os itens de "_revisar"
constantes: variables_info.json
# documentacao: manual.md      # fórmulas do fabricante (recomendado)
# exemplo: rd80                # plug-in parecido já validado
pontos_de_controle: []         # valores conhecidos, ex.:
#  - {{tempo: "2024-12-16 14:01:00", variavel: rain_rate, valor: 0.2432}}
max_iteracoes: 5
"""


def escrever_molde(esq: dict, const: dict, saida: str | Path, nome: str) -> list[Path]:
    saida = Path(saida)
    saida.mkdir(parents=True, exist_ok=True)
    feitos = []
    for arq, dado in (("netCDF_info.json", esq), ("variables_info.json", const)):
        p = saida / arq
        p.write_text(json.dumps(dado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        feitos.append(p)
    t = saida / "tarefa.yaml"
    if not t.exists():                      # não apaga uma tarefa já editada
        t.write_text(TAREFA.format(pasta=saida, nome=nome), encoding="utf-8")
        feitos.append(t)
    (saida / "amostras").mkdir(exist_ok=True)
    return feitos


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    fonte = ap.add_mutually_exclusive_group(required=True)
    fonte.add_argument("--de-netcdf", help="NetCDF de exemplo (ARM ou outro)")
    fonte.add_argument("--de-dod", help="produto da ARM, ex.: ld.b1 (rede ou cache)")
    fonte.add_argument("--dod-json", help="DOD bruto salvo em arquivo")
    ap.add_argument("--versao", help="versão do DOD (padrão: a mais recente)")
    ap.add_argument("--cache", default="data/arm_extra/cache", help="cache do ArmClient")
    ap.add_argument("--saida", required=True)
    ap.add_argument("--nome", help="nome da tarefa (padrão: nome da pasta de saída)")
    ap.add_argument("--datastream")
    ap.add_argument("--site")
    ap.add_argument("--facility")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--alt", type=float)
    a = ap.parse_args()
    if a.de_netcdf:
        esq, const = molde_de_netcdf(a.de_netcdf)
    else:
        if a.dod_json:
            bruto, proc = json.loads(Path(a.dod_json).read_text(encoding="utf-8")), \
                Path(a.dod_json).stem
        else:
            from geracao_netcdf.arm_services import ArmClient, fetch_dod
            proc = a.de_dod
            bruto = fetch_dod(ArmClient(cache_dir=a.cache), proc)
        esq, const = molde_de_dod(bruto, proc, a.versao)
    _ajustar(esq, a.datastream, a.site, a.facility, a.lat, a.lon, a.alt)
    feitos = escrever_molde(esq, const, a.saida, a.nome or Path(a.saida).name)
    print(f"{len(esq['variables'])} variáveis, {len(esq['global attributes'])} atributos globais")
    for p in feitos:
        print("  ", p)
    print("Revise:")
    for r in esq["_revisar"]:
        print("  -", r)


if __name__ == "__main__":
    main()
