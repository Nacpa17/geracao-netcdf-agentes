"""Benchmark de formatos: o circuito inteiro com respostas gravadas."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("netCDF4")
pd = pytest.importorskip("pandas")

from conftest import GERACAO_MINUTOS, GERACAO_POR_ARQUIVO  # noqa: E402
from geracao_netcdf.aparelhos import carregar  # noqa: E402
from geracao_netcdf.bench import rodar  # noqa: E402
from geracao_netcdf.sintetico import VARIANTES, constantes_rd80, escrever_variante  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ROTEIRO = "roteiro:" + str(ROOT / "benchmarks" / "geracao" / "solucoes")
KW = dict(n_minutos=GERACAO_MINUTOS, minutos_por_arquivo=GERACAO_POR_ARQUIVO,
          log=lambda *a: None)


def test_todos_os_formatos_guardam_o_mesmo_dado(rd80_minutos, tmp_path):
    const = constantes_rd80()
    canonico = None
    for nome in VARIANTES:
        arquivos = escrever_variante(nome, rd80_minutos, tmp_path / nome, GERACAO_POR_ARQUIVO)
        sol = ROOT / "benchmarks" / "geracao" / "solucoes"
        plugin = carregar(str(sol / (f"{nome}.py" if nome != "hora_local"
                                     else "hora_local/2_converte_para_utc.py")))
        df = plugin.ler(arquivos, const)[["Interval"] + [f"n{i}" for i in range(1, 21)]]
        df = df.astype(float)
        df.index = pd.DatetimeIndex(df.index).as_unit("ns")
        if canonico is None:
            canonico = df
        else:
            pd.testing.assert_frame_equal(df, canonico, check_names=False, check_freq=False)


def test_benchmark_com_roteiro_acerta_tudo(tmp_path):
    r = rodar(ROTEIRO, pasta=tmp_path, **KW)
    assert r["corretas"] == r["variantes"] == len(VARIANTES)
    linhas = {x["variante"]: x for x in r["linhas"]}
    assert linhas["hora_local"]["iteracoes"] == 2          # 1ª esquece o fuso
    assert all(x["iteracoes"] == 1 for k, x in linhas.items() if k != "hora_local")
    assert linhas["iso_utc"]["variaveis"] == "52/52" and linhas["iso_utc"]["dias"] == "2/2"
    assert (tmp_path / "resumo.md").read_text(encoding="utf-8").count("| sim |") == 7


def test_sem_pontos_de_controle_o_esquema_nao_basta(tmp_path):
    """Sem os pontos de controle, o arquivo 4 h deslocado passa no validador
    de esquema — mas não no gabarito. Por isso os pontos existem."""
    r = rodar(ROTEIRO, variantes=["hora_local"], pasta=tmp_path, max_iteracoes=1,
              controle=False, **KW)
    x = r["linhas"][0]
    assert x["agente_ok"] and not x["correto"]
    assert x["avaliacao"]["erros_referencia"] > 0


def test_modo_e_variante_invalidos(tmp_path):
    with pytest.raises(ValueError):
        rodar(ROTEIRO, modo="outro", pasta=tmp_path)
    with pytest.raises(ValueError):
        rodar(ROTEIRO, variantes=["nao_existe"], pasta=tmp_path)
