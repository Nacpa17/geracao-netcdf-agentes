"""Física do Parsivel² (geracao_netcdf/aparelhos/parsivel2/fisica.py) em casos de conta à mão.

A conferência com os arquivos da ARM (erro relativo ~1e-7) é feita por
scripts/conferir_parsivel_arm.py, que precisa dos arquivos ld.b1."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from geracao_netcdf.aparelhos import listar  # noqa: E402
from geracao_netcdf.aparelhos.parsivel2 import fisica  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_densidade_soma_velocidades_e_usa_v_do_tamanho():
    dD, v = np.array([0.25, 0.25]), np.array([4.0, 6.5])     # classes de 1 e 2 mm
    c = np.zeros((1, 3, 2))                  # (tempo, velocidade, tamanho)
    c[0, :, 0] = [3, 5, 2]                    # 10 gotas de 1 mm em 3 classes de velocidade
    c[0, 1, 1] = 2                            # 2 gotas de 2 mm
    N = fisica.densidade_numerica(c, dD, v)
    k = fisica.AREA_NOMINAL * fisica.INTERVALO
    assert N[0].tolist() == pytest.approx([10 / (k * 4.0 * 0.25), 2 / (k * 6.5 * 0.25)])
    c[0, 0, 0] = -1                           # contagem inválida: a linha vira NaN
    assert np.isnan(fisica.densidade_numerica(c, dD, v)).all()


def test_momentos_e_exponencial_exata():
    # exponencial N0 = 8000, Lambda = 2: M3 = N0 3!/L^4 = 3000; M6 = N0 6!/L^7 = 45000
    lam, n0, mvd, dm = fisica.exponencial(np.array([3000.0, 0.0]), np.array([45000.0, 1.0]))
    assert lam[0] == pytest.approx(2.0) and n0[0] == pytest.approx(8000.0)
    assert mvd[0] == pytest.approx(1.835) and dm[0] == pytest.approx(2.0)
    assert np.isnan(lam[1])
    N, D, dD = np.array([[2.0, 1.0]]), np.array([1.0, 2.0]), np.array([0.5, 0.5])
    assert fisica.momento(N, D, dD, 3)[0] == pytest.approx(2 * 0.5 + 8 * 0.5)
    assert fisica.conteudo_agua(N, D, dD)[0] == pytest.approx(np.pi / 6 * 5.0)
    assert fisica.refletividade_dbz(N, D, dD)[0] == pytest.approx(10 * np.log10(2 * 1 * 0.5 + 1 * 64 * 0.5))
    assert fisica.diametros_extremos(N, D) == (pytest.approx([1.0]), pytest.approx([2.0]))
    vazio = fisica.diametros_extremos(np.zeros((1, 2)), D)
    assert np.isnan(vazio[0]).all() and np.isnan(vazio[1]).all()


def test_ainda_nao_e_aparelho_registrado_e_molde_do_atto_existe():
    assert "parsivel2" not in listar()        # falta o `ler` do formato do ATTO
    pasta = ROOT / "aparelhos_novos" / "parsivel2"
    esq = json.loads((pasta / "netCDF_info.json").read_text(encoding="utf-8"))
    v = esq["variables"]
    assert v["raw_spectrum"]["dimensions"] == ["time", "raw_fall_velocity", "particle_size"]
    assert v["fall_velocity_calculated"]["dimensions"] == ["particle_size"]
    assert esq["global attributes"]["effective_measurement_area"]["value"] == "0.0054 m^2"
    const = json.loads((pasta / "variables_info.json").read_text(encoding="utf-8"))
    assert len(const["particle_size"]) == 32 and const["integration_time"] == 60
    assert "documentacao" in (pasta / "tarefa.yaml").read_text(encoding="utf-8")
