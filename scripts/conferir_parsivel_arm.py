"""Confere geracao_netcdf/aparelhos/parsivel2/fisica.py com arquivos ld.b1 da ARM.

    python scripts/conferir_parsivel_arm.py sgpldE13.b1.20230828.000000.cdf ...

Recalcula, a partir do raw_spectrum (lido como [tempo, velocidade, tamanho]),
as variáveis que o ingest da ARM deriva e mostra o maior erro relativo de
cada uma nos minutos com partículas. Também mostra, para as duas ordens
possíveis dos eixos, a velocidade média observada por classe de tamanho.
"""
import sys
from pathlib import Path

import netCDF4
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geracao_netcdf.aparelhos.parsivel2 import fisica  # noqa: E402


def main(arquivos):
    for f in arquivos:
        with netCDF4.Dataset(f) as nc:
            nc.set_auto_mask(False)

            def g(n):
                x = np.asarray(nc[n][:], dtype=float)
                mv = getattr(nc[n], "missing_value", None)
                return np.where(x == float(mv), np.nan, x) if mv is not None else x

            bruto = g("raw_spectrum")                     # declarado (t, tamanho, velocidade)
            D, dD = g("particle_size"), g("class_size_width")
            v_d, V = g("fall_velocity_calculated"), g("raw_fall_velocity")
            cvd = bruto                                   # na prática: (t, velocidade, tamanho)
            N = fisica.densidade_numerica(cvd, dD, v_d)
            com = np.nansum(cvd, axis=(1, 2)) > 0
            print(f"{Path(f).name}: {com.sum()} minutos com partículas")
            if not com.any():
                continue
            m3, m6 = fisica.momento(N, D, dD, 3), fisica.momento(N, D, dD, 6)
            lam, n0, mvd, dm = fisica.exponencial(m3, m6)
            dmin, dmax = fisica.diametros_extremos(N, D)
            pares = [("number_density_drops", N), ("liquid_water_content", fisica.conteudo_agua(N, D, dD)),
                     ("equivalent_radar_reflectivity", fisica.refletividade_dbz(N, D, dD)),
                     ("moment3", m3), ("moment6", m6), ("slope_parameter", lam),
                     ("intercept_parameter", n0), ("median_volume_diameter", mvd),
                     ("liquid_water_distribution_mean", dm), ("diameter_min", dmin),
                     ("diameter_max", dmax)]
            for nome, nosso in pares:
                arm = g(nome)
                ok = com.reshape((-1,) + (1,) * (arm.ndim - 1)) & np.isfinite(arm) & np.isfinite(nosso) & (arm != 0)
                rel = np.abs(nosso[ok] / arm[ok] - 1) if ok.any() else np.array([np.nan])
                print(f"   {nome:32s} erro relativo máx {np.nanmax(rel):.1e}")
            tot = np.nansum(cvd[com], axis=0)            # (velocidade, tamanho)
            for rot, M in (("como declarado (tamanho, velocidade)", tot), ("[velocidade, tamanho]", tot.T)):
                tem = M.sum(axis=1) > 20
                vm = (M[tem] * V).sum(axis=1) / M[tem].sum(axis=1)
                amostra = ", ".join(f"{d:.2f} mm: {a:.1f}/{b:.1f}" for d, a, b in
                                    list(zip(D[tem], vm, v_d[tem]))[::3])
                print(f"   ordem {rot}: v média observada / Lhermitte -> {amostra}")


if __name__ == "__main__":
    main(sys.argv[1:])
