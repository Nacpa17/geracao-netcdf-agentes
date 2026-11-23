"""Perfil "ARM Data File Standards 1.3" do validador (DOE/SC-ARM-15-004, 2020).

Usado com `python -m geracao_netcdf.validador ... --padrao arm`. Confere o que o
documento da ARM pede além do CF; cada mensagem cita a seção. O produto do
ATTO é "inspirado na ARM" (não publicado pela ARM), então isto é um
checklist do que falta para a conformidade, não uma exigência do projeto.

Códigos ARM_E_* são o que o padrão marca como obrigatório; ARM_A_* são
recomendações ou itens que dependem de contexto (ex.: dado instantâneo).
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

QC_DESCRICAO = ("This variable contains bit packed integer values, where each bit "
                "represents a QC test on the data. Non-zero bits indicate the QC "
                "condition given in the description for those bits; a value of 0 "
                "(no bits set) indicates the data has not failed any QC tests.")
QC_DESCRICAO_GLOBAL = "See global attributes for individual QC bit descriptions."
# versão 1.2 do texto (a 1.3 trocou "field" por "variable", seção 3.1)
QC_DESCRICAO_12 = QC_DESCRICAO.replace("This variable contains", "This field contains")
# 6.7.1: obrigatórios (input_source: ingest que lê dado bruto). Conferido com o
# Parsivel² da ARM (sgpldE13.b1, ARM-1.2), que traz exatamente estes
GLOBAIS_OBRIGATORIOS = ("command_line", "Conventions", "process_version", "dod_version",
                        "input_source", "site_id", "platform_id", "facility_id",
                        "data_level", "location_description", "datastream",
                        "sampling_interval", "doi", "history")
GLOBAIS_RECOMENDADOS = ("averaging_interval", "serial_number", "title", "description",
                        "doi_url")
NOME_ARQUIVO = re.compile(r"^(?P<ds>[a-z0-9]+[A-Z][A-Za-z]?[0-9]*\.[a-z0-9]{2})\."
                          r"(?P<data>\d{8})\.(?P<hora>\d{6})\.(nc|cdf)$")


def _p(codigo: str, mensagem: str, variavel: str | None = None):
    from geracao_netcdf.validador import Problema
    erro = codigo.startswith("ARM_E")
    return Problema(codigo, "erro" if erro else "aviso", mensagem, variavel)


def _norm(texto: str) -> str:
    """Compara textos ignorando caixa, espaços e aspas tipográficas."""
    texto = texto.replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"\s+", " ", texto).strip().lower()


def _attrs(v) -> dict:
    return {a: v.getncattr(a) for a in v.ncattrs()}


def checar(nc, arquivo: Path) -> list:
    out: list = []
    g = _attrs(nc)
    v = nc.variables

    # 4.0 formato
    if nc.data_model not in ("NETCDF3_CLASSIC", "NETCDF3_64BIT_OFFSET", "NETCDF3_64BIT_DATA",
                             "NETCDF4_CLASSIC"):
        out.append(_p("ARM_A_FORMATO", f"formato {nc.data_model}: a ARM prefere netCDF-3, "
                      "ou NETCDF4_CLASSIC para compressão (4.0)"))

    # 5.1 nome do arquivo
    m = NOME_ARQUIVO.match(arquivo.name)
    if not m:
        out.append(_p("ARM_A_NOME", "nome fora do padrão (sss)(inst)(Fn).(dl).(yyyymmdd)."
                      "(hhmmss).nc, só [a-zA-Z0-9.] e minúsculas exceto a facility (5.1)"))
    elif isinstance(g.get("datastream"), str) and m.group("ds") != g["datastream"]:
        out.append(_p("ARM_A_NOME", f"nome começa com {m.group('ds')!r}, datastream é "
                      f"{g['datastream']!r} (5.1)"))

    # 6.1 tempo
    if "time" in nc.dimensions:
        if not nc.dimensions["time"].isunlimited():
            out.append(_p("ARM_E_TEMPO", "a dimensão time deve ser UNLIMITED (6.1.1)"))
        for nome, var in v.items():
            if "time" in var.dimensions and var.dimensions[0] != "time":
                out.append(_p("ARM_E_TEMPO", "time deve ser a primeira dimensão (6.1.1)", nome))
    for nome in ("base_time", "time_offset", "time"):
        if nome not in v:
            out.append(_p("ARM_E_TEMPO", f"falta {nome} (base_time + time_offset e time são "
                          "obrigatórios, 6.1.1)"))
    if "base_time" in v:
        bt = v["base_time"]
        if bt.dimensions or np.dtype(bt.dtype).kind not in "iu":
            out.append(_p("ARM_E_TEMPO", "base_time deve ser escalar inteiro (6.1.2)", "base_time"))
        if "string" not in bt.ncattrs():
            out.append(_p("ARM_A_TEMPO", "base_time sem o atributo string (6.1.2)", "base_time"))
    for nome, outra in (("base_time", "time_offset"), ("time_offset", "base_time")):
        if nome in v and outra in v and outra not in str(getattr(v[nome], "ancillary_variables", "")).split():
            out.append(_p("ARM_A_TEMPO", f"ancillary_variables deveria ligar {nome} a {outra} "
                          "(6.1.2)", nome))
    if "time" in v:
        t = v["time"]
        if getattr(t, "standard_name", None) != "time":
            out.append(_p("ARM_A_TEMPO", "time sem standard_name = \"time\" (6.1.3)", "time"))
        if "bounds" not in t.ncattrs():
            out.append(_p("ARM_A_TEMPO", "sem time_bounds: obrigatório para dado não "
                          "instantâneo (integrado, média) (6.1.4)", "time"))

    # 6.2 coordenadas
    for nome in nc.dimensions:
        if nome in v and v[nome].dimensions == (nome,):
            c = v[nome]
            a = _attrs(c)
            if any(k in a for k in ("missing_value", "_FillValue")):
                out.append(_p("ARM_E_COORD", "coordenada não pode ter missing_value/_FillValue "
                              "(6.2, 6.6.2)", nome))
            arr = np.asarray(c[:])
            if arr.dtype.kind in "fiu" and arr.size > 1:
                d = np.diff(arr.astype(float))
                if np.isnan(arr.astype(float)).any() or not (np.all(d > 0) or np.all(d < 0)):
                    out.append(_p("ARM_E_COORD", "coordenada deve ser monotônica e sem NaN (6.2)",
                                  nome))

    # 6.3 local
    for nome, unidade, padrao in (("lat", "degree_N", "latitude"),
                                  ("lon", "degree_E", "longitude"),
                                  ("alt", "m", "altitude")):
        if nome not in v:
            out.append(_p("ARM_E_LOCAL", f"falta a variável {nome} (6.3)"))
            continue
        a = _attrs(v[nome])
        if a.get("units") != unidade:
            out.append(_p("ARM_E_LOCAL" if nome != "alt" else "ARM_A_LOCAL",
                          f"units deveria ser {unidade!r} (6.3)", nome))
        if a.get("standard_name") != padrao:
            out.append(_p("ARM_E_LOCAL", f"standard_name deveria ser {padrao!r} (6.3)", nome))

    # 6.6 atributos de variável
    nomes_longos: dict[str, list[str]] = {}
    for nome, var in v.items():
        a = _attrs(var)
        if isinstance(a.get("long_name"), str):
            nomes_longos.setdefault(a["long_name"], []).append(nome)
        if a.get("units") == "unitless":
            out.append(_p("ARM_A_UNIDADE", "\"unitless\" virou \"1\" no padrão 1.3 (3.1, 6.6.1)",
                          nome))
    for ln, quem in nomes_longos.items():
        if len(quem) > 1:
            out.append(_p("ARM_E_LONG_NAME", f"long_name {ln!r} repetido em "
                          + ", ".join(quem) + " (deve ser único, 6.6.1)"))

    # 6.8 QC
    globais_bits = {k for k in g if re.fullmatch(r"qc_bit_\d+_description", k)}
    for nome, var in v.items():
        a = _attrs(var)
        bits = sorted(int(m.group(1)) for k in a if (m := re.fullmatch(r"bit_(\d+)_description", k)))
        avaliados = any(re.fullmatch(r"bit_\d+_assessment", k) for k in a)
        e_qc = a.get("flag_method") == "bit" or (nome.startswith("qc_") and (
            avaliados or ((bits or globais_bits) and "flag_masks" not in a)))
        if not e_qc:
            if nome.startswith("qc_") and "flag_masks" in a:
                out.append(_p("ARM_A_QC", "indicador de estado (flag_masks) com prefixo qc_: a "
                              "ARM reserva qc_ para resultados de testes (6.5, 6.8.2)", nome))
            continue
        base = nome[3:] if nome.startswith("qc_") else None
        alvo = v.get(base) if base else None
        ln = str(a.get("long_name", ""))
        if alvo is not None:
            prefixo = "Quality check results on variable: "
            if ln.startswith("Quality check results on field: "):
                out.append(_p("ARM_A_QC", "long_name com a redação da versão 1.2 (\"on field:\"); "
                              "a 1.3 usa \"on variable:\" (3.1, 6.8.2)", nome))
                ln = prefixo + ln[len("Quality check results on field: "):]
            if not ln.startswith(prefixo):
                out.append(_p("ARM_E_QC", f"long_name deve ser {prefixo!r} + long_name de "
                              f"{base} (6.8.2)", nome))
            elif _norm(ln[len(prefixo):]) != _norm(str(getattr(alvo, "long_name", ""))):
                out.append(_p("ARM_A_QC", f"long_name não repete o de {base} (6.8.2)", nome))
            if base != "time" and nome not in str(getattr(alvo, "ancillary_variables", "")).split():
                out.append(_p("ARM_E_QC", f"{base}:ancillary_variables deve citar {nome} "
                              "(6.8.2)", base))
        elif not ln.startswith("Quality check results"):
            out.append(_p("ARM_E_QC", "QC de várias variáveis: long_name deve ser \"Quality "
                          "check results\" e o nome não pode ser qc_<variável existente> "
                          "(6.8.9)", nome))
        elif base and ln != "Quality check results":
            out.append(_p("ARM_A_QC", f"o nome sugere QC de {base!r}, que não existe no "
                          "arquivo: use qc_<variável> ou long_name \"Quality check results\" "
                          "(6.8.2, 6.8.9)", nome))
        if a.get("units") != "1":
            out.append(_p("ARM_E_QC", "units deve ser \"1\" (6.8.2)", nome))
        if a.get("flag_method") != "bit":
            out.append(_p("ARM_E_QC", "falta flag_method = \"bit\" (6.8.2)", nome))
        desc = _norm(str(a.get("description", "")))
        if desc == _norm(QC_DESCRICAO_12):
            out.append(_p("ARM_A_QC", "description com a redação da versão 1.2 (\"This field "
                          "contains\") (3.1, 6.8.2)", nome))
        elif desc not in (_norm(QC_DESCRICAO), _norm(QC_DESCRICAO_GLOBAL)):
            out.append(_p("ARM_E_QC", "description deve ser o texto padrão da ARM (ou apontar "
                          "para os atributos globais) (6.8.2, 6.8.7)", nome))
        if a.get("standard_name") != "quality_flag":
            out.append(_p("ARM_A_QC", "standard_name \"quality_flag\" recomendado (6.8.2)", nome))
        if np.dtype(var.dtype).kind not in "iu":
            out.append(_p("ARM_E_QC", "QC deve ser inteiro (6.8.2)", nome))
        elif np.dtype(var.dtype).itemsize > 4:
            out.append(_p("ARM_A_QC", "QC de 64 bits: a ARM recomenda inteiro de 32 bits, que "
                          "também cabe em netCDF-3 (6.8.2)", nome))
        for b in bits:
            aval = a.get(f"bit_{b}_assessment")
            if aval not in ("Bad", "Indeterminate"):
                out.append(_p("ARM_E_QC", f"bit_{b}_assessment deve ser \"Bad\" ou "
                              f"\"Indeterminate\" (está {aval!r}) (6.8.3)", nome))

    # 6.7 globais
    for k in GLOBAIS_OBRIGATORIOS:
        valor = g.get(k)
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            out.append(_p("ARM_E_GLOBAL", f"falta o atributo global {k} (6.7.1; sem valor "
                          "conhecido use \"N/A\" ou \"unknown\")"))
    for k in GLOBAIS_RECOMENDADOS:
        if k not in g:
            out.append(_p("ARM_A_GLOBAL", f"atributo global recomendado ausente: {k} (6.7.1)"))
    conv = str(g.get("Conventions", ""))
    if conv and not re.search(r"\bARM-\d+(\.\d+)?\b", conv):
        out.append(_p("ARM_A_GLOBAL", f"Conventions = {conv!r} não declara a versão ARM "
                      "(\"ARM-1.3 ...\", 6.7.1)"))
    dl = g.get("data_level")
    if isinstance(dl, str) and not re.fullmatch(r"[0-9a-z]{2}", dl):
        out.append(_p("ARM_A_GLOBAL", f"data_level = {dl!r}: a ARM usa só o código (ex.: "
                      "\"b1\") (5.1.3, 6.7.1)"))
    partes = [g.get(k) for k in ("site_id", "platform_id", "facility_id", "data_level")]
    if all(isinstance(x, str) for x in partes) and isinstance(g.get("datastream"), str):
        esperado = f"{partes[0]}{partes[1]}{partes[2]}.{partes[3]}"
        if g["datastream"] != esperado:
            out.append(_p("ARM_A_GLOBAL", f"datastream {g['datastream']!r} deveria ser "
                          f"site_id + platform_id + facility_id + \".\" + data_level = "
                          f"{esperado!r} (6.7.1)"))
    doi = g.get("doi")
    if isinstance(doi, str) and doi.lower().startswith(("http", "doi:")):
        out.append(_p("ARM_A_GLOBAL", f"doi = {doi!r}: a ARM guarda só o número (ex.: "
                      "\"10.5439/1039926\") e a URL em doi_url (6.7.1)"))
    if list(g)[-1:] != ["history"]:
        out.append(_p("ARM_A_GLOBAL", "history deveria ser o último atributo global (6.7.1)"))
    return out
