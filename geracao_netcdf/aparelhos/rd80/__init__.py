"""Plug-in do disdrômetro Joss-Waldvogel RD-80 (Distromet, arquivos DISDRODATA).

Formato bruto: texto separado por TAB, uma linha por minuto, vírgula decimal:
    YYYY-MM-DD  hh:mm:ss  Status  Interval [s]  n1 ... n20  RI [mm/h]  RA [mm]  RAT [mm]

Física (guia do DISDRODATA 2.0, apêndice 4.3; as mesmas do RD80-Processing do
grupo): para as contagens n_i de cada classe (diâmetro médio D_i em mm,
velocidade de queda v_i em m/s, largura dD_i em mm — apêndice 4.2), área do
sensor A (m², 0,005 = 50 cm²) e intervalo dt (s):

    N(D_i) = n_i / (A dt v_i dD_i)                    [1/(m^3 mm)]
    R      = (pi/6) 3.6e-3 / (A dt) * sum(n_i D_i^3)  [mm/h]
    W      = (pi/6) / (A dt) * sum(n_i/v_i D_i^3) / 1000   [g/m^3]
    Z      = 1 / (A dt) * sum(n_i/v_i D_i^6)          [mm^6/m^3] -> dBZ
    E_k    = (pi/12) 1e-6 / A * sum(n_i D_i^3 v_i^2)   [J/m^2 no intervalo]
    fluxo  = E_k * 3600 / dt                          [J/(m^2 h)]
    Lambda = (6!/pi * W_mm3 / Z)^(1/3);  N0 = W_mm3 Lambda^4 / pi   (Marshall-Palmer)

Validação: reproduzem o exemplo de saída do próprio software do fabricante
(guia do DISDRODATA 2.0, apêndices 4.4-4.5) até a última casa impressa — ver
tests/test_geracao_rd80_fabricante.py; aplicadas aos dados da ARM do mesmo
instrumento, reproduzem as variáveis publicadas pela ARM até o
arredondamento (4ª casa); e a taxa de chuva bate com o RI que o próprio
RD-80 grava.

Horário: o DISDRODATA grava o INÍCIO de cada intervalo (guia 2.0, seção
2.3.1: "A time stamp marks always the beginning of an interval"); time_bounds
guarda [início observado, início + Interval].
"""

from __future__ import annotations

from math import factorial, pi

import numpy as np
import pandas as pd


def _ler_um(arquivo, colunas):
    df = pd.read_csv(arquivo, sep="\t", header=None, names=colunas, skiprows=1,
                     dtype=str, keep_default_na=False)
    if df.shape[1] != len(colunas) or df[colunas[-1]].eq("").all():
        # sem TAB: separador de espaço simples (como o código original)
        df = pd.read_csv(arquivo, sep=r"\s", engine="python", header=None,
                         names=colunas, skiprows=1, dtype=str, keep_default_na=False)
    tempo = pd.to_datetime(df["Date"].str.strip() + " " + df["Time"].str.strip(),
                           format="%Y-%m-%d %H:%M:%S")
    df = df.drop(columns=["Date", "Time"])
    for c in df.columns:
        if c == "Status":
            texto = df[c].str.strip()
            df[c] = texto.where(texto != "")          # vazio -> ausente
        else:
            df[c] = pd.to_numeric(df[c].str.strip().str.replace(",", ".", regex=False),
                                  errors="coerce")
    df.index = pd.DatetimeIndex(tempo, name="time")
    return df


def ler(arquivos, const):
    colunas = list(const["Columns"])
    df = pd.concat([_ler_um(a, colunas) for a in arquivos])
    fuso = const.get("time_zone", "UTC")
    if fuso and fuso.upper() != "UTC":
        df.index = df.index.tz_localize(fuso).tz_convert("UTC").tz_localize(None)
    return df


def dimensoes(const):
    return {"drop_avg_class": np.asarray(const["drop_mean_diam"], dtype=float)}


def _classe_extrema(contagens, diametros, ultima):
    tem = np.nan_to_num(contagens, nan=0.0) > 0
    qualquer = tem.any(axis=1) & np.all(np.isfinite(contagens), axis=1)
    if ultima:
        idx = tem.shape[1] - 1 - np.argmax(tem[:, ::-1], axis=1)
    else:
        idx = np.argmax(tem, axis=1)
    return np.where(qualquer, diametros[idx], np.nan)


def calcular(grade, const):
    D = np.asarray(const["drop_mean_diam"], dtype=float)
    v = np.asarray(const["fall_vell"], dtype=float)
    dD = np.asarray(const["delta_diam"], dtype=float)
    A = float(const["sensor_area"])
    dados = grade.dados
    n = dados[list(const["drop_col"])].to_numpy(dtype=float)        # (T, classes)
    dt = dados["Interval"].to_numpy(dtype=float)

    contagens_ok = np.all(np.isfinite(n) & (n >= 0), axis=1)
    dt_ok = np.isfinite(dt) & (dt > 0)
    nc = np.where(contagens_ok[:, None], n, np.nan)    # só linhas válidas entram
    dtc = np.where(dt_ok, dt, np.nan)
    k = 1.0 / (A * dtc)

    total = nc.sum(axis=1)
    # (mesma ordem de operações do RD80-Processing: resultado idêntico)
    rain_rate = (pi / 6.0) * (3.6 / 10.0**3) * k * (nc * D**3).sum(axis=1)
    liq_water = (pi / 6.0) * k * (nc / v * D**3).sum(axis=1) / 1000.0
    z = k * (nc / v * D**6).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        dbz = np.where(z > 0, 10.0 * np.log10(z), np.nan)
        energia = (pi / 12.0) * (1.0 / A) * (1.0 / 10**6) * (nc * D**3 * v**2).sum(axis=1)
        fluxo = energia * (3600.0 / dtc)
        w_mm3 = liq_water * 1000.0
        ok = np.isfinite(z) & (z > 0) & (w_mm3 > 0)
        slope = np.where(ok, np.power(factorial(6) / pi * (w_mm3 / np.where(ok, z, 1.0)),
                                      1.0 / 3.0), np.nan)
        n0 = np.where(ok, (1.0 / pi) * (factorial(6) / pi) ** (4.0 / 3.0) * w_mm3
                      * np.power(np.where(ok, w_mm3 / z, 1.0), 4.0 / 3.0), np.nan)
        densidade = nc / ((A * dtc[:, None]) * (v * dD))

    qc_part = np.zeros(len(grade), dtype=np.int64)
    qc_part[total == 0] |= 1
    qc_part[(total > 0) & (total < float(const.get("filter_ni", 0)))] |= 2
    qc_part[~contagens_ok] |= 4
    qc_part[~dt_ok] |= 8

    # mesmos bits do RD80-Processing 3.1 (4 e 5 são de agregação, que o núcleo não faz)
    passo = float(grade.passo_s)
    qc_si = np.zeros(len(grade), dtype=np.int64)
    qc_si[grade.presente & ~dt_ok] |= 1
    with np.errstate(invalid="ignore"):
        qc_si[grade.presente & dt_ok & (dt < passo)] |= 2
        qc_si[grade.presente & dt_ok & (dt > passo)] |= 4
    qc_si[~grade.presente] |= 32

    # pelo início observado (UTC), como no 3.1; sem observação fica 0
    inicio = grade.inicio_obs if grade.inicio_obs is not None else grade.tempos
    qc_cal = np.zeros(len(grade), dtype=np.int64)
    for i, data in enumerate(sorted(const.get("calibration_date") or [])):
        depois = grade.presente & np.asarray(pd.DatetimeIndex(inicio) >= pd.Timestamp(data))
        qc_cal[depois] |= 1 << i

    return {
        # início real da integração (o DISDRODATA marca o INÍCIO: guia 2.0,
        # seção 2.3.1) até início + Interval
        "time_bounds": grade.limites(np.where(dt_ok, dt, np.nan)),
        "fall_velocity": v,
        "delta_diam": dD,
        "drop_avg_class": D,
        "num_drop": n,
        "number_detected_drops": total,
        "qc_number_detected_particles": qc_part,
        "diam_max": _classe_extrema(nc, D, ultima=True),
        "diam_min": _classe_extrema(nc, D, ultima=False),
        "num_drop_density": densidade,
        "rain_rate": rain_rate,
        "radar_reflectivity": dbz,
        "liq_water": liq_water,
        "energy_flux": fluxo,
        "slope_parameter": slope,
        "distribution_intercept": n0,
        "sample_interval": dt,
        "qc_sample_interval": qc_si,
        "qc_calibration": qc_cal,
    }
