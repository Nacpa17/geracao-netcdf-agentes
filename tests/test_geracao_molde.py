"""Molde de esquema a partir de um NetCDF de exemplo ou do DOD da ARM."""

from __future__ import annotations

import json
import sys

import pytest

pytest.importorskip("netCDF4")

from fake_arm import DOD_1TWRMR_C1  # noqa: E402
from geracao_netcdf import molde  # noqa: E402
from geracao_netcdf.aparelhos import caminho_do_aparelho, carregar  # noqa: E402
from geracao_netcdf.esquema import carregar_constantes, carregar_esquema  # noqa: E402
from geracao_netcdf.gerar import gerar  # noqa: E402
from geracao_netcdf.molde import (_numero, escrever_molde, molde_de_dod,  # noqa: E402
                               molde_de_netcdf)
from geracao_netcdf.validador import validar  # noqa: E402


def test_molde_de_netcdf_ida_e_volta(rd80_netcdf, rd80_brutos, tmp_path):
    esq, const = molde_de_netcdf(rd80_netcdf[0])
    escrever_molde(esq, const, tmp_path / "m", "teste")
    esquema = carregar_esquema(tmp_path / "m" / "netCDF_info.json")
    # o esquema tirado do arquivo aprova o próprio arquivo...
    for f in rd80_netcdf:
        r = validar(f, esquema)
        assert r.aprovado and r.nota == 100, r.texto()
    # ...e serve para o gerador produzir arquivos válidos de novo
    rel = gerar(carregar("rd80"), rd80_brutos, esquema,
                carregar_constantes(caminho_do_aparelho("rd80") / "variables_info.json"),
                tmp_path / "saida")
    assert len(rel) == 2 and all(validar(r["arquivo"], esquema).aprovado for r in rel)
    assert const["integration_time"] == 60
    assert const["drop_avg_class"][0] == 0.359            # float32 com grafia curta
    assert esq["variables"]["lat"]["value"] == -2.1815896
    g = esq["global attributes"]
    assert g["history"]["value"] == g["input_sha256"]["value"] == "__AUTO__"
    assert "units" in esq["variables"]["time"] and "YYYY" in esq["variables"]["time"]["units"]


def test_ajustes_e_lista_de_revisao(rd80_netcdf):
    esq, _ = molde_de_netcdf(rd80_netcdf[0])
    molde._ajustar(esq, "attldCA.b1", None, None, -2.14, None, None)
    assert esq["global attributes"]["datastream"]["value"] == "attldCA.b1"
    assert esq["variables"]["lat"]["value"] == -2.14
    rev = " ".join(esq["_revisar"])
    assert "site_id" in rev and "variável lon" in rev and "variável lat" not in rev
    assert "datastream" not in rev


def test_molde_de_dod():
    esq, const = molde_de_dod(DOD_1TWRMR_C1, "1twrmr.c1")
    assert esq["global attributes"]["dod_version"]["value"] == "1twrmr-c1-1.1"   # mais recente
    assert esq["global attributes"]["doi"]["value"] == "10.5439/0000001"
    v = esq["variables"]
    assert set(v) == {"mixing_ratio", "rh", "temp", "qc_rh", "station_id"}
    assert v["temp"]["units"] == "degC" and v["temp"]["Datatype"] == "float32"
    assert v["station_id"]["Datatype"] == "str" and "units" not in v["station_id"]
    for k in ("command_line", "history", "input_source", "process_version"):
        assert esq["global attributes"][k]["value"] == "__AUTO__"
    assert molde_de_dod(DOD_1TWRMR_C1, "1twrmr.c1", "1.0")[0]["variables"]["temp"]["units"] == "K"
    with pytest.raises(ValueError):
        molde_de_dod({}, "nada.b1")


def test_numero():
    assert _numero("-9999") == -9999 and _numero("0.5") == 0.5
    assert _numero("1, 2, 4") == [1, 2, 4] and _numero("abc") == "abc"


def test_cli_nao_apaga_tarefa_editada(rd80_netcdf, tmp_path, monkeypatch, capsys):
    saida = tmp_path / "novo"
    argv = ["molde", "--de-netcdf", str(rd80_netcdf[0]), "--saida", str(saida),
            "--datastream", "attxyzCA.b1"]
    monkeypatch.setattr(sys, "argv", argv)
    molde.main()
    assert "Revise:" in capsys.readouterr().out
    assert (saida / "amostras").is_dir()
    (saida / "tarefa.yaml").write_text("nome: editada\n", encoding="utf-8")
    molde.main()
    assert (saida / "tarefa.yaml").read_text(encoding="utf-8") == "nome: editada\n"
    d = json.loads((saida / "netCDF_info.json").read_text(encoding="utf-8"))
    assert d["global attributes"]["datastream"]["value"] == "attxyzCA.b1"
