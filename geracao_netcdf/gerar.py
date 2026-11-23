"""Gera os NetCDFs diários de um aparelho: plug-in + esquema -> arquivos.

Uso:
    # aparelho registrado (esquema e constantes vêm da pasta do plug-in)
    python -m geracao_netcdf.gerar --aparelho rd80 --entrada pasta_dos_txt --saida saida/rd80

    # um dia só (lê todos os arquivos, inclusive os vizinhos que cruzam a meia-noite)
    python -m geracao_netcdf.gerar --aparelho rd80 --entrada pasta --saida saida --dia 16/12/2024

    # plug-in avulso (ex.: escrito pelo agente) com esquema/constantes próprios
    python -m geracao_netcdf.gerar --modulo plugin.py --esquema netCDF_info.json \\
        --constantes variables_info.json --entrada amostras --saida saida --validar

Dias sem nenhuma observação não geram arquivo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from geracao_netcdf import VERSAO
from geracao_netcdf.aparelhos import caminho_do_aparelho, carregar
from geracao_netcdf.escrita import escrever_netcdf
from geracao_netcdf.esquema import Esquema, carregar_constantes, carregar_esquema
from geracao_netcdf.grade import (dias_com_dados, montar_grade,
                               preparar_observacoes)


def listar_arquivos(entradas: list[str], extensao: str | None) -> list[Path]:
    arquivos: list[Path] = []
    for e in entradas:
        p = Path(e)
        if p.is_dir():
            arquivos += sorted(x for x in p.iterdir() if x.is_file()
                               and not x.name.startswith(".")
                               and (not extensao or x.name.endswith(extensao)))
        elif p.exists():
            arquivos.append(p)
        else:
            raise FileNotFoundError(f"entrada não encontrada: {e}")
    return arquivos


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def gerar(plugin, arquivos: list[Path], esquema: Esquema, const: dict,
          saida: str | Path, dias: list | None = None,
          linha_de_comando: str | None = None) -> list[dict]:
    """Roda o plug-in e grava um NetCDF por dia com dados. Devolve um
    relatório por arquivo gerado."""
    if not arquivos:
        raise ValueError("nenhum arquivo de entrada")
    passo = int(const.get("integration_time", 60))
    obs = plugin.ler(list(arquivos), const)
    if not isinstance(obs, pd.DataFrame):
        raise TypeError("`ler` deve devolver um pandas.DataFrame")
    obs = preparar_observacoes(obs, passo)
    datastream = esquema.datastream or "dados"
    dinamicos = {
        "command_line": linha_de_comando or " ".join(shlex.quote(a) for a in sys.argv),
        "process_version": VERSAO,
        "input_source": ", ".join(a.name for a in arquivos),
        "input_sha256": json.dumps({a.name: _sha256(a) for a in arquivos}),
        "sampling_interval": f"{passo} seconds",
        "averaging_interval": "None",
    }
    if const.get("sensor_area") is not None:
        dinamicos["effective_measurement_area"] = f"{const['sensor_area']} m^2"
    if const.get("calibration_date"):
        dinamicos["calib_date"] = " ".join(sorted(const["calibration_date"]))

    alvo = [pd.Timestamp(d).normalize() for d in dias] if dias else dias_com_dados(obs)
    # dimensão "bound" (início/fim) dos time_bounds: não tem variável própria
    usa_bound = any("bound" in v.dims for n, v in esquema.variaveis.items()
                    if n not in esquema.proibidas)
    relatorios = []
    for dia in alvo:
        grade = montar_grade(obs, dia, passo)
        if grade.n_obs == 0:
            print(f"{dia:%d/%m/%Y}: nenhuma observação — arquivo não gerado")
            continue
        dims = {"time": grade.segundos, **plugin.dimensoes(const)}
        if usa_bound:
            dims.setdefault("bound", 2)
        variaveis = {"base_time": int(grade.dia.timestamp()),
                     "time_offset": grade.segundos, "time": grade.segundos,
                     "qc_time": grade.qc_time}
        if "time_bounds" in esquema.variaveis:
            variaveis["time_bounds"] = grade.limites()   # o plug-in pode refinar
        calculadas = plugin.calcular(grade, const)
        if not isinstance(calculadas, dict):
            raise TypeError("`calcular` deve devolver um dict {variavel: array}")
        variaveis.update(calculadas)
        dinamicos["history"] = (f"created {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC "
                                f"by geracao_netcdf ({VERSAO})")
        nome = f"{datastream}.{dia:%Y%m%d}.000000.nc"
        rel = escrever_netcdf(Path(saida) / nome, esquema, dims, variaveis, dia,
                              passo, dinamicos)
        rel["dia"] = f"{dia:%Y-%m-%d}"
        rel["observacoes"] = int(grade.n_obs)
        rel["passos_com_dado"] = int(grade.presente.sum())
        relatorios.append(rel)
        print(f"{dia:%d/%m/%Y}: {nome} ({grade.presente.sum()}/{len(grade)} passos com dado)")
    return relatorios


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aparelho", help="plug-in registrado (ex.: rd80)")
    ap.add_argument("--modulo", help="plug-in avulso (.py)")
    ap.add_argument("--esquema", help="netCDF_info.json (padrão: o do aparelho)")
    ap.add_argument("--constantes", help="variables_info.json (padrão: o do aparelho)")
    ap.add_argument("--entrada", nargs="+", required=True,
                    help="arquivos ou pastas com os dados brutos")
    ap.add_argument("--saida", required=True, help="pasta dos NetCDFs")
    ap.add_argument("--dia", action="append",
                    help="dd/mm/aaaa (pode repetir); padrão: todos os dias com dado")
    ap.add_argument("--validar", action="store_true",
                    help="valida cada arquivo gerado (código de saída 1 se houver erro)")
    a = ap.parse_args()
    if not a.aparelho and not a.modulo:
        ap.error("informe --aparelho ou --modulo")
    plugin = carregar(a.modulo or a.aparelho)
    pasta = caminho_do_aparelho(a.aparelho) if a.aparelho else None
    esquema_p = a.esquema or (pasta / "netCDF_info.json" if pasta else None)
    const_p = a.constantes or (pasta / "variables_info.json" if pasta else None)
    if not esquema_p or not const_p:
        ap.error("com --modulo, informe também --esquema e --constantes")
    esquema = carregar_esquema(esquema_p)
    const = carregar_constantes(const_p)
    arquivos = listar_arquivos(a.entrada, const.get("input_file_extension"))
    dias = [datetime.strptime(d, "%d/%m/%Y") for d in a.dia] if a.dia else None
    linha = "python -m geracao_netcdf.gerar " + " ".join(shlex.quote(x) for x in sys.argv[1:])
    relatorios = gerar(plugin, arquivos, esquema, const, a.saida, dias, linha)
    if not relatorios:
        print("Nenhum arquivo gerado.")
        sys.exit(2)
    if a.validar:
        from geracao_netcdf.validador import validar
        com_erro = 0
        for r in relatorios:
            res = validar(r["arquivo"], esquema)
            print(res.texto(curto=True))
            com_erro += bool(res.erros)
        sys.exit(1 if com_erro else 0)


if __name__ == "__main__":
    main()
