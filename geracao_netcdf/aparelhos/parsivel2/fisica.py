"""Física do Parsivel² (OTT), conferida com o ingest da ARM (ld.b1, DOD ld-b1-2.0).

Ainda não é um plug-in registrado: falta o `ler` do formato bruto do ATTO
(é o que o agente vai escrever). Estas funções são a parte reaproveitável do
`calcular` (o agente pode importá-las: `from geracao_netcdf.aparelhos.parsivel2.fisica
import ...`). Conferência: `python scripts/conferir_parsivel_arm.py sgpldE13.b1.*.cdf`.

O que a conferência com os dois arquivos sgpldE13.b1 de exemplo mostrou:

- number_density_drops da ARM é reproduzido com erro relativo de 3e-8 por
      N(D_i) = sum_j c[j, i] / (A * dt * v(D_i) * dD_i)
  com A = 0,0054 m² (54 cm², a área NOMINAL — não a efetiva 180*(30 - D/2) mm²
  citada no manual TR-137), dt = 60 s, v(D_i) = fall_velocity_calculated
  (Lhermitte) da classe de TAMANHO e c[j, i] a contagem na classe de
  velocidade j e de tamanho i;
- no arquivo da ARM, raw_spectrum está guardado como [tempo, velocidade,
  tamanho], embora as dimensões declaradas sejam (time, particle_size,
  raw_fall_velocity): na ordem declarada, gotas de 3 mm cairiam a 0,6 m/s; na
  ordem [velocidade, tamanho], a velocidade média de cada classe segue a curva
  de Lhermitte. fall_velocity_calculated também é por classe de tamanho,
  apesar de declarada na dimensão raw_fall_velocity;
- liquid_water_content = (pi/6) M3, momentk = sum N D^k dD e
  equivalent_radar_reflectivity = 10 log10(M6): idênticos;
- slope, intercept, median_volume_diameter e liquid_water_distribution_mean
  pela exponencial ajustada aos momentos 3 e 6 (Lambda = (120 M3/M6)^(1/3),
  N0 = M3 Lambda^4 / 6, MVD = 3,67/Lambda, Dm = 4/Lambda): a 0,2 % da ARM
  (a ARM usa alguma constante ligeiramente diferente);
- diameter_min/diameter_max: menor/maior classe com N > 0: idênticos.
"""

from __future__ import annotations

import numpy as np

AREA_NOMINAL = 0.0054          # m² (54 cm²), ARM TR-137
INTERVALO = 60.0               # s


def densidade_numerica(contagens_vd, larguras, velocidade_d, dt=INTERVALO,
                       area=AREA_NOMINAL) -> np.ndarray:
    """N(D) [1/(m³ mm)]. `contagens_vd`: (..., velocidade, tamanho); as outras
    entradas são por classe de tamanho. Contagem ausente (NaN ou < 0) vira NaN
    na linha inteira."""
    c = np.asarray(contagens_vd, dtype=float)
    invalida = ~np.isfinite(c) | (c < 0)
    n = c.sum(axis=-2)
    n = np.where(invalida.any(axis=(-2, -1))[..., None], np.nan, n)
    return n / (area * dt * np.asarray(velocidade_d, float) * np.asarray(larguras, float))


def momento(N, diametros, larguras, k: int) -> np.ndarray:
    """M_k = sum N D^k dD  [mm^k/m³]."""
    return (np.asarray(N) * np.asarray(diametros) ** k * np.asarray(larguras)).sum(axis=-1)


def conteudo_agua(N, diametros, larguras) -> np.ndarray:
    """LWC [mm³/m³] = (pi/6) M3."""
    return np.pi / 6.0 * momento(N, diametros, larguras, 3)


def refletividade_dbz(N, diametros, larguras) -> np.ndarray:
    z = momento(N, diametros, larguras, 6)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(z > 0, 10.0 * np.log10(z), np.nan)


def exponencial(m3, m6):
    """(Lambda [1/mm], N0 [1/(m³ mm)], MVD [mm], Dm [mm]) da exponencial pelos
    momentos 3 e 6; NaN onde M3 ou M6 não são positivos."""
    m3, m6 = np.asarray(m3, float), np.asarray(m6, float)
    ok = (m3 > 0) & (m6 > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        lam = np.where(ok, np.cbrt(120.0 * m3 / np.where(ok, m6, 1.0)), np.nan)
        return lam, m3 * lam**4 / 6.0, 3.67 / lam, 4.0 / lam


def diametros_extremos(N, diametros):
    """(menor, maior) classe com N > 0; NaN sem partículas."""
    N = np.nan_to_num(np.asarray(N, float), nan=0.0)
    d = np.asarray(diametros, float)
    tem = N > 0
    algum = tem.any(axis=-1)
    menor = np.where(algum, d[np.argmax(tem, axis=-1)], np.nan)
    maior = np.where(algum, d[tem.shape[-1] - 1 - np.argmax(tem[..., ::-1], axis=-1)], np.nan)
    return menor, maior
