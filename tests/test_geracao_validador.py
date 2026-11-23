"""Validador de NetCDF: aprova o que está certo e acusa cada defeito plantado."""

from __future__ import annotations

import shutil
import sys

import pytest

netCDF4 = pytest.importorskip("netCDF4")
np = pytest.importorskip("numpy")

from geracao_netcdf import validador  # noqa: E402
from geracao_netcdf.aparelhos import caminho_do_aparelho  # noqa: E402
from geracao_netcdf.esquema import carregar_esquema  # noqa: E402
from geracao_netcdf.validador import _normalizar_unidade, validar  # noqa: E402

ESQUEMA = carregar_esquema(caminho_do_aparelho("rd80") / "netCDF_info.json")


@pytest.fixture
def copia(rd80_netcdf, tmp_path):
    destino = tmp_path / rd80_netcdf[0].name
    shutil.copyfile(rd80_netcdf[0], destino)
    return destino


def _mexer(caminho, funcao):
    with netCDF4.Dataset(caminho, "a") as nc:
        nc.set_auto_mask(False)
        funcao(nc)


def _codigos(r):
    return {p.codigo for p in r.problemas}


def test_arquivo_certo_passa_e_compara_igual(copia, rd80_netcdf):
    r = validar(copia, ESQUEMA, referencia=rd80_netcdf[0])
    assert r.aprovado and r.nota == 100
    assert r.comparacao["identicas"] == r.comparacao["comparadas"] == 26


@pytest.mark.parametrize("defeito, codigo", [
    (lambda nc: nc["qc_calibration"].setncattr("valid_max", "N/A"), "E_ATTR_TIPO"),
    (lambda nc: nc["rain_rate"].setncattr("missing_value", np.float64(-9999)), "E_ATTR_TIPO"),
    (lambda nc: nc["rain_rate"].setncattr("ancillary_variables", "qc_time nao_existe"),
     "E_ANCILLARY"),
    (lambda nc: nc["rain_rate"].delncattr("units"), "E_ATTR_FALTANDO"),
    (lambda nc: nc.setncattr("datastream", "__AUTO__"), "E_GLOBAL_PLACEHOLDER"),
    (lambda nc: nc.delncattr("site_id"), "E_GLOBAL_FALTANDO"),
])
def test_erros_de_metadados(copia, defeito, codigo):
    _mexer(copia, defeito)
    r = validar(copia, ESQUEMA)
    assert codigo in _codigos(r) and not r.aprovado, r.texto()


def test_erros_de_valores(copia):
    def plantar(nc):
        rr = nc["rain_rate"][:]
        rr[100] = np.nan                                  # NaN em vez de missing_value
        nc["rain_rate"][:] = rr
        q = nc["qc_number_detected_particles"][:]
        q[5] = 16                                         # bit que ninguém descreveu
        nc["qc_number_detected_particles"][:] = q
        nc["qc_time"].setncattr("valid_max", np.int64(3))   # 8 (sem observação) fica fora
        t = nc["time"][:]
        t[10], t[11] = t[11], t[10]                       # tempo fora de ordem
        nc["time"][:] = t
    _mexer(copia, plantar)
    c = _codigos(validar(copia, ESQUEMA))
    assert {"E_NAN", "E_QC_BITS", "E_FAIXA", "E_TEMPO"} <= c


def test_avisos(copia):
    def plantar(nc):
        nc.renameAttribute("Conventions", "conventions")
        nc["rain_rate"].setncattr("Variable ID", "713")
        nc["distribution_intercept"].setncattr("units", "1/(m^3-mm)")
        nc.setncattr("sampling_interval", "60")
    _mexer(copia, plantar)
    r = validar(copia, ESQUEMA)
    assert {"A_CONVENTIONS", "A_ATTR_INTERNO", "A_UNIDADES_MISTAS",
            "A_GLOBAL_SEM_UNIDADE"} <= _codigos(r)
    assert r.aprovado and r.nota == 100 - 3 * len(r.avisos)


def test_variavel_proibida_extra_e_faltando(copia):
    def plantar(nc):
        nc.renameVariable("diam_min", "diametro_minimo")
    _mexer(copia, plantar)
    c = _codigos(validar(copia, ESQUEMA))
    assert {"E_VAR_FALTANDO", "A_VAR_EXTRA"} <= c


def test_comparacao_acusa_um_valor_diferente(copia, rd80_netcdf):
    def plantar(nc):
        rr = nc["rain_rate"][:]
        i = int(np.argmax(rr))
        rr[i] = rr[i] * 1.01
        nc["rain_rate"][:] = rr
    _mexer(copia, plantar)
    r = validar(copia, ESQUEMA, referencia=rd80_netcdf[0])
    assert r.comparacao["diferentes"] == {"rain_rate": 1}
    assert "R_DIFERENCA" in _codigos(r)


def test_arquivo_vazio_e_arquivo_invalido(tmp_path):
    p = tmp_path / "vazio.nc"
    with netCDF4.Dataset(p, "w") as nc:
        nc.createDimension("time", None)
        t = nc.createVariable("time", "f8", ("time",))
        t.units, t.long_name = "seconds since 2024-01-01 00:00:00 0:00", "Time"
        x = nc.createVariable("x", "f4", ("time",), fill_value=False)
        x.units, x.long_name, x.missing_value = "mm", "X", np.float32(-9999)
        t[:] = [0.0, 60.0]
        x[:] = [-9999, -9999]
        nc.Conventions, nc.history = "CF-1.8", "teste"
    assert "E_VAZIO" in _codigos(validar(p))
    ruim = tmp_path / "texto.nc"
    ruim.write_text("isto não é NetCDF")
    r = validar(ruim)
    assert _codigos(r) == {"E_ARQUIVO"} and r.nota == 85


def test_estilo_arm_com_bits_globais_nao_gera_aviso(tmp_path):
    p = tmp_path / "arm.nc"
    with netCDF4.Dataset(p, "w") as nc:
        nc.createDimension("time", 2)
        t = nc.createVariable("time", "f8", ("time",))
        t.units, t.long_name = "seconds since 2024-01-01 00:00:00 0:00", "Time"
        q = nc.createVariable("qc_x", "i4", ("time",))
        q.units, q.long_name = "unitless", "QC"
        n = nc.createVariable("num_drop", "f4", ("time",))
        n.units, n.long_name = "none", "Contagem"            # a ARM usa assim
        t[:] = [0.0, 60.0]
        q[:] = [0, 3]
        n[:] = [1.0, 2.0]
        nc.qc_bit_1_description = "teste 1"
        nc.qc_bit_2_description = "teste 2"
        nc.Conventions, nc.history = "ARM-1.2", "teste"
    r = validar(p)
    assert r.aprovado and r.nota == 100, r.texto()


def test_unidades_normalizadas():
    assert _normalizar_unidade("mm h-1") == _normalizar_unidade("mm/h")
    assert _normalizar_unidade("1/(m^3 mm)") == _normalizar_unidade("1/(m^3-mm)")
    assert _normalizar_unidade("m-3") != _normalizar_unidade("m^3")


def test_cli_codigo_de_saida(copia, tmp_path, monkeypatch, capsys):
    rel = tmp_path / "rel.json"
    monkeypatch.setattr(sys, "argv", ["validador", str(copia), "--aparelho", "rd80",
                                      "--json", str(rel)])
    with pytest.raises(SystemExit) as e:
        validador.main()
    assert e.value.code == 0 and "APROVADO" in capsys.readouterr().out and rel.exists()
    _mexer(copia, lambda nc: nc["rain_rate"].delncattr("long_name"))
    with pytest.raises(SystemExit) as e:
        validador.main()
    assert e.value.code == 1


def test_limites_de_tempo_em_outra_referencia(copia):
    """Como no RD80-Processing 3.1: time_bounds em segundos desde 1970 sem units
    (um leitor CF os lê como segundos desde a meia-noite)."""
    _mexer(copia, lambda nc: nc["time_bounds"].__setitem__(
        slice(None), nc["time_bounds"][:] + float(nc["base_time"][...])))
    r = validar(copia, ESQUEMA)
    assert "E_TEMPO_LIMITES" in _codigos(r) and "outra referência" in r.texto()


def test_limites_e_tempo_ausentes(copia):
    def plantar(nc):
        tb = nc["time_bounds"][:]
        tb[0] = -9999.0
        nc["time_bounds"][:] = tb
        nc["time_bounds"].setncattr("missing_value", -9999.0)
        t = nc["time"][:]
        t[5] = np.nan
        nc["time"][:] = t
    _mexer(copia, plantar)
    r = validar(copia, ESQUEMA)
    textos = " ".join(p.mensagem for p in r.erros)
    assert "não podem faltar" in textos and "ausente ou NaN" in textos


def test_padrao_arm(copia):
    r = validar(copia, ESQUEMA, padrao="arm")
    assert not r.erros, r.texto()          # só avisos: escolhas do grupo (data_level, doi...)
    assert {"ARM_A_GLOBAL", "ARM_A_QC"} <= _codigos(r)

    def plantar(nc):
        nc["qc_sample_interval"].delncattr("flag_method")
        nc["qc_sample_interval"].setncattr("bit_1_assessment", "Ruim")
        nc.delncattr("location_description")
        nc["lat"].delncattr("standard_name")
        nc["rain_rate"].setncattr("long_name", nc["liq_water"].long_name)
    _mexer(copia, plantar)
    msgs = " ".join(p.mensagem for p in validar(copia, ESQUEMA, padrao="arm").erros)
    for trecho in ("flag_method", "bit_1_assessment", "location_description", "latitude",
                   "repetido"):
        assert trecho in msgs, trecho
    with pytest.raises(ValueError):
        validar(copia, padrao="iso")
