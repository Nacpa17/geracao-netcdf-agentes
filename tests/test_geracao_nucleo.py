"""Núcleo da geração de NetCDF: grade de tempo, qc_time, escrita e plug-in rd80."""

from __future__ import annotations

from math import pi
from pathlib import Path
from types import SimpleNamespace

import pytest

netCDF4 = pytest.importorskip("netCDF4")
np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

from geracao_netcdf.aparelhos import caminho_do_aparelho, carregar, listar  # noqa: E402
from geracao_netcdf.escrita import ErroEsquema  # noqa: E402
from geracao_netcdf.esquema import carregar_constantes, carregar_esquema, vazio  # noqa: E402
from geracao_netcdf.gerar import gerar  # noqa: E402
from geracao_netcdf.grade import (BIT_MAIOR, BIT_REPETIDO, BIT_SEM_OBS, dias_com_dados,  # noqa: E402
                               montar_grade, nominal, preparar_observacoes)
from geracao_netcdf.validador import validar  # noqa: E402

PASTA = caminho_do_aparelho("rd80")
ESQUEMA = carregar_esquema(PASTA / "netCDF_info.json")
CONST = carregar_constantes(PASTA / "variables_info.json")


def _obs(horarios, valores=None):
    idx = pd.DatetimeIndex(pd.to_datetime(horarios))
    return pd.DataFrame({"x": valores if valores is not None else np.arange(len(idx), dtype=float)},
                        index=idx)


def test_nominal_meio_passo_vai_para_frente():
    idx = pd.DatetimeIndex(pd.to_datetime(["2024-01-01 00:00:29", "2024-01-01 00:00:30",
                                           "2024-01-01 00:00:31"]))
    assert list(nominal(idx, 60).strftime("%H:%M")) == ["00:00", "00:01", "00:01"]


def test_grade_repetido_lacuna_e_sem_observacao():
    obs = preparar_observacoes(_obs(["2024-01-01 00:00", "2024-01-01 00:01",
                                     "2024-01-01 00:01", "2024-01-01 00:05"],
                                    [10.0, 11.0, 99.0, 15.0]), 60)
    g = montar_grade(obs, "2024-01-01", 60)
    assert len(g) == 1440 and g.n_obs == 4
    assert list(g.presente[:6]) == [True, True, False, False, False, True]
    assert g.qc_time[0] == 0
    assert g.qc_time[1] == BIT_REPETIDO                 # repetido: fica o primeiro
    assert g.dados["x"].iloc[1] == 11.0
    assert list(g.qc_time[2:5]) == [BIT_SEM_OBS] * 3
    assert g.qc_time[5] == BIT_MAIOR                    # havia lacuna antes
    assert np.isnan(g.dados["x"].iloc[2])


def test_grade_atravessa_meia_noite_e_ordena():
    obs = preparar_observacoes(_obs(["2024-01-02 00:01", "2024-01-01 23:58",
                                     "2024-01-01 23:59", "2024-01-02 00:00"]), 60)
    dias = dias_com_dados(obs)
    assert [f"{d:%Y-%m-%d}" for d in dias] == ["2024-01-01", "2024-01-02"]
    g2 = montar_grade(obs, dias[1], 60)
    assert g2.qc_time[0] == 0          # o anterior (23:59) veio do outro dia
    assert list(g2.presente[:3]) == [True, True, False]


def test_preparar_aceita_fuso_e_rejeita_indice_errado():
    idx = pd.DatetimeIndex(["2024-01-01 00:00"]).tz_localize("America/Manaus")
    obs = preparar_observacoes(pd.DataFrame({"x": [1.0]}, index=idx), 60)
    assert f"{obs.index[0]:%H:%M}" == "04:00" and obs.index.tz is None
    with pytest.raises(TypeError):
        preparar_observacoes(pd.DataFrame({"x": [1.0]}), 60)


def test_esquema_rd80_limpo():
    assert "rd80" in listar()
    assert len(ESQUEMA.obrigatorias) == 26 and not ESQUEMA.proibidas
    assert ESQUEMA.datastream == "attrd80CA.b1"
    for v in ESQUEMA.variaveis.values():
        assert not ({"Name", "Variable ID", "Description"} & set(v.atributos))
    assert vazio("__AUTO__") and vazio("TBD") and not vazio("none") and not vazio([1])


def test_gerar_rd80_e_validar(rd80_netcdf):
    nomes = [p.name for p in rd80_netcdf]
    assert nomes == ["attrd80CA.b1.20241215.000000.nc", "attrd80CA.b1.20241216.000000.nc"]
    for p in rd80_netcdf:
        r = validar(p, ESQUEMA)
        assert r.aprovado and r.nota == 100, r.texto()


def test_metadados_tipados_e_globais(rd80_netcdf):
    with netCDF4.Dataset(rd80_netcdf[0]) as nc:
        for nome, v in nc.variables.items():
            assert "_FillValue" not in v.ncattrs(), nome
            for a in ("missing_value", "valid_min", "valid_max", "flag_masks"):
                if a in v.ncattrs():
                    assert np.asarray(v.getncattr(a)).dtype == v.dtype, (nome, a)
        g = {k: nc.getncattr(k) for k in nc.ncattrs()}
        assert "Conventions" in g and "conventions" not in g
        assert g["sampling_interval"] == "60 seconds"
        assert g["effective_measurement_area"] == "0.005 m^2"
        assert "geracao_netcdf" in g["history"] and g["input_source"].startswith("RD-")
        assert "__AUTO__" not in str(g.values())
        assert nc["time"].units == "seconds since 2024-12-15 00:00:00 0:00"
        assert nc["drop_avg_class"].units == "mm"
        assert nc.dimensions["time"].isunlimited()


def test_dia_sem_dado_nao_gera_arquivo(rd80_brutos, tmp_path, capsys):
    rel = gerar(carregar("rd80"), rd80_brutos, ESQUEMA, CONST, tmp_path,
                dias=[pd.Timestamp("2024-12-20")])
    assert rel == [] and not list(tmp_path.glob("*.nc"))
    assert "nenhuma observação" in capsys.readouterr().out


def test_rd80_bate_com_RI_e_com_as_formulas(rd80_minutos, rd80_netcdf):
    D = np.asarray(CONST["drop_mean_diam"])
    v = np.asarray(CONST["fall_vell"])
    A = CONST["sensor_area"]
    with netCDF4.Dataset(rd80_netcdf[0]) as nc:
        nc.set_auto_mask(False)
        t = pd.Timestamp("2024-12-15") + pd.to_timedelta(nc["time"][:], unit="s")
        rr = pd.Series(nc["rain_rate"][:], index=t)
        dados = {k: pd.Series(nc[k][:], index=t) for k in
                 ("liq_water", "radar_reflectivity", "energy_flux", "slope_parameter",
                  "distribution_intercept", "diam_max", "diam_min", "number_detected_drops")}
        qc = pd.Series(nc["qc_number_detected_particles"][:], index=t)
    brutos = rd80_minutos[~rd80_minutos.index.duplicated()]
    brutos = brutos[brutos.index < pd.Timestamp("2024-12-16")]
    # a taxa calculada bate com o RI (4 casas) que o "aparelho" gravou
    assert np.allclose(rr.loc[brutos.index], brutos["RI"], atol=1e-4)
    # e as outras variáveis com as fórmulas da DOCUMENTACAO.md
    quando = brutos["RI"].idxmax()
    n = brutos.loc[quando, [f"n{i}" for i in range(1, 21)]].to_numpy(float)
    k = 1 / (A * 60.0)
    w = (pi / 6) * k * (n / v * D**3).sum() / 1000
    z = k * (n / v * D**6).sum()
    w3 = 1000 * w
    esperado = {
        "liq_water": w, "radar_reflectivity": 10 * np.log10(z),
        "energy_flux": (pi / 12) / A * 1e-6 * (n * D**3 * v**2).sum() * 3600 / 60,
        "slope_parameter": (720 / pi * w3 / z) ** (1 / 3),
        "distribution_intercept": (1 / pi) * (720 / pi) ** (4 / 3) * w3 * (w3 / z) ** (4 / 3),
        "diam_max": D[np.nonzero(n)[0].max()], "diam_min": D[np.nonzero(n)[0].min()],
        "number_detected_drops": n.sum()}
    for nome, valor in esperado.items():
        assert dados[nome].loc[quando] == pytest.approx(valor, rel=1e-5), nome
    # QC: lacuna = 12 (contagens e intervalo ausentes); seco = 1
    lacuna = t[~t.isin(brutos.index)]
    assert (qc.loc[lacuna] == 12).all()
    seco = brutos.index[brutos[[f"n{i}" for i in range(1, 21)]].sum(axis=1) == 0]
    assert (qc.loc[seco] == 1).all()


def test_faltou_variavel_obrigatoria(rd80_brutos, tmp_path):
    rd80 = carregar("rd80")

    def calcular(grade, const):
        r = rd80.calcular(grade, const)
        r.pop("rain_rate")
        return r

    plugin = SimpleNamespace(ler=rd80.ler, dimensoes=rd80.dimensoes, calcular=calcular)
    with pytest.raises(ErroEsquema, match="rain_rate"):
        gerar(plugin, rd80_brutos, ESQUEMA, CONST, tmp_path)


def test_forma_errada_vira_erro_legivel(rd80_brutos, tmp_path):
    rd80 = carregar("rd80")

    def calcular(grade, const):
        r = rd80.calcular(grade, const)
        r["rain_rate"] = r["rain_rate"][:10]
        return r

    plugin = SimpleNamespace(ler=rd80.ler, dimensoes=rd80.dimensoes, calcular=calcular)
    with pytest.raises(ErroEsquema, match="forma"):
        gerar(plugin, rd80_brutos, ESQUEMA, CONST, tmp_path)


def test_carregar_plugin_avulso(tmp_path):
    p = tmp_path / "meu.py"
    p.write_text("def ler(a, c):\n    return None\n")
    with pytest.raises(AttributeError, match="dimensoes"):
        carregar(str(p))
    p.write_text("from geracao_netcdf.aparelhos.rd80 import ler, dimensoes, calcular\n")
    assert callable(carregar(str(p)).calcular)
    assert Path(caminho_do_aparelho("rd80") / "DOCUMENTACAO.md").exists()


def test_time_bounds_e_qc_no_padrao_arm(rd80_netcdf):
    """ARM Data File Standards 1.3: time_bounds (6.1.4), QC (6.8.2), tempo (6.1)."""
    with netCDF4.Dataset(rd80_netcdf[1]) as nc:          # 16/12: só 00:00-04:52 com dado
        nc.set_auto_mask(False)
        t, tb, si = nc["time"][:], nc["time_bounds"][:], nc["sample_interval"][:]
        assert nc["time"].bounds == "time_bounds" and nc["time"].standard_name == "time"
        assert len(nc.dimensions["bound"]) == 2 and "bound" not in nc.variables
        assert set(nc["time_bounds"].ncattrs()) <= {"long_name", "comment"}
        com = si != -9999
        assert 0 < com.sum() < len(t)
        assert np.array_equal(tb[com, 0], t[com])
        assert np.array_equal(tb[com, 1] - tb[com, 0], si[com])
        assert np.all(tb[~com, 1] - tb[~com, 0] == 60)       # sem dado: passo nominal
        for q in ("qc_time", "qc_number_detected_particles", "qc_sample_interval"):
            a = {k: nc[q].getncattr(k) for k in nc[q].ncattrs()}
            assert a["units"] == "1" and a["flag_method"] == "bit", q
            assert a["standard_name"] == "quality_flag", q
            assert a["long_name"].startswith("Quality check results on variable: "), q
            assert a["description"].startswith("This variable contains bit packed"), q
        assert nc["qc_sample_interval"].bit_4_description == "Not used"
        assert list(nc["qc_calibration"].flag_masks) == [1, 2, 4]
        assert nc["base_time"].ancillary_variables == "time_offset"
        assert nc["time_offset"].ancillary_variables == "base_time"
        assert "missing_value" not in nc["drop_avg_class"].ncattrs()
        assert nc.location_description and list(nc.ncattrs())[-1] == "history"


def test_qc_sample_interval_e_limites_por_passo():
    """Mesmos bits do RD80-Processing 3.1: 1 inválido, 2 abaixo, 4 acima, 32 sem dado."""
    rd80 = carregar("rd80")
    idx = pd.to_datetime(["2024-01-01 00:00", "2024-01-01 00:01", "2024-01-01 00:02",
                          "2024-01-01 00:03"])
    contagens = {f"n{i}": [0.0] * 4 for i in range(1, 21)}
    obs = pd.DataFrame({"Interval": [60.0, 59.0, 61.0, np.nan], **contagens}, index=idx)
    g = montar_grade(preparar_observacoes(obs, 60), "2024-01-01", 60)
    r = rd80.calcular(g, CONST)
    assert list(r["qc_sample_interval"][:5]) == [0, 2, 4, 1, 32]
    assert list(r["qc_calibration"][:5]) == [1, 1, 1, 1, 0]
    tb = r["time_bounds"]
    assert tb[1].tolist() == [60.0, 119.0] and tb[2].tolist() == [120.0, 181.0]
    assert tb[3].tolist() == [180.0, 240.0] and tb[4].tolist() == [240.0, 300.0]
