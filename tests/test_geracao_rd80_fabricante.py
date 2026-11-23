"""O plug-in rd80 reproduz o exemplo de saída do próprio software do fabricante.

Fonte: DISTROMET, User Guide for DISDRODATA 2.0 (2009), apêndice 4.4 (arquivo
gravado: contagens, RI, RA, RAT) e 4.5 (resultados: Dmax, Wg, ZdB, EF, N0,
Lambda, N(Di)), três minutos de 14/02/2007.

O guia não imprime as contagens das classes 11 a 13. Elas são as únicas
inteiras (busca em 0..199) que reproduzem ao mesmo tempo RI, Wg, ZdB e EF
impressos; o teste `test_contagens_omitidas_sao_determinadas` refaz a conta
por mínimos quadrados. Com elas, TODOS os valores impressos batem até a
última casa, exceto: (a) N0 do 2º minuto, 1212,97163 contra 1212,9717
impresso (6e-8 relativo: arredondamento do software); (b) a coluna impressa
como "N(d7)", que não corresponde a nenhuma classe — erro de diagramação do
guia (N(d7) = n7 / (F t v7 dD7) daria 156,7; 39,2; 26,1).
"""

from __future__ import annotations

import pytest

netCDF4 = pytest.importorskip("netCDF4")
np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

from geracao_netcdf.aparelhos import caminho_do_aparelho, carregar  # noqa: E402
from geracao_netcdf.esquema import carregar_constantes, carregar_esquema  # noqa: E402
from geracao_netcdf.gerar import gerar  # noqa: E402

PASTA = caminho_do_aparelho("rd80")
CONST = carregar_constantes(PASTA / "variables_info.json")

# n1..n20 (n11-n13 recuperadas, ver docstring) e os valores impressos no guia
EXEMPLO = [
    ("19:47:00", [0, 50, 3, 7, 9, 23, 48, 47, 51, 48, 23, 1, 2, 2, 2, 4, 4, 6, 6, 4],
     dict(RI=19.7447, RA=0.3291, RAT=0.3291, Dmax=5.373, Wg=0.7154, ZdB=49.6027,
          EF=669.2336, N0=497.3545, L=1.2157),
     {1: 0, 2: 895.0949, 3: 48.4738, 4: 72.8375, 5: 84.9262, 6: 119.9183,
      17: 3.5884, 18: 5.002, 19: 3.8525, 20: 3.2072}),
    ("19:48:00", [0, 7, 4, 1, 1, 4, 12, 13, 21, 33, 71, 7, 6, 10, 10, 11, 18, 31, 38, 42],
     dict(RI=103.7771, RA=1.7296, RAT=2.0587, Dmax=5.373, Wg=3.286, ZdB=58.286,
          EF=4091.533, N0=1212.9717, L=1.0377),
     {1: 0, 2: 125.3133, 3: 64.6317, 4: 10.4054, 5: 9.4362, 6: 20.8554,
      17: 16.148, 18: 25.8437, 19: 24.399, 20: 33.6754}),
    ("19:49:00", [0, 48, 2, 2, 3, 13, 8, 23, 17, 50, 62, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     dict(RI=5.0533, RA=0.0842, RAT=2.1429, Dmax=1.912, Wg=0.2417, ZdB=33.873,
          EF=89.4366, N0=4945.7646, L=2.8316),
     {1: 0, 2: 859.2911, 3: 32.3159, 4: 20.8107, 5: 28.3087, 6: 67.7799,
      17: 0, 18: 0, 19: 0, 20: 0}),
]
VARIAVEL = {"RI": "rain_rate", "Dmax": "diam_max", "Wg": "liq_water",
            "ZdB": "radar_reflectivity", "EF": "energy_flux",
            "N0": "distribution_intercept", "L": "slope_parameter"}


def _casas(x: float) -> int:
    s = repr(float(x))
    return 0 if s.endswith(".0") else len(s.split(".")[1])


def _tol(impresso: float) -> float:
    """Meia unidade da última casa impressa + a precisão do float32 do NetCDF."""
    return 0.5 * 10 ** -_casas(impresso) + 3e-7 * abs(impresso)


@pytest.fixture(scope="module")
def netcdf(tmp_path_factory):
    pasta = tmp_path_factory.mktemp("fabricante")
    cab = "\t".join(["YYYY-MM-DD", "hh:mm:ss", "Status", "Interval [s]",
                     *[f"n{i}" for i in range(1, 21)], "RI [mm/h]", "RA [mm]", "RAT [mm]"])
    linhas = [cab]
    for hora, n, imp, _ in EXEMPLO:
        linhas.append("\t".join(["2007-02-14", hora, "", "60", *map(str, n),
                                 *(f"{imp[k]:.4f}".replace(".", ",") for k in ("RI", "RA", "RAT"))]))
    bruto = pasta / "RD-070214-194700.txt"
    bruto.write_bytes(("\r\n".join(linhas) + "\r\n").encode("latin-1"))
    rel = gerar(carregar("rd80"), [bruto], carregar_esquema(PASTA / "netCDF_info.json"),
                CONST, pasta / "nc", linha_de_comando="pytest")
    assert len(rel) == 1
    return rel[0]["arquivo"]


def _valores(arquivo, nome, horas):
    with netCDF4.Dataset(arquivo) as nc:
        nc.set_auto_mask(False)
        t = pd.Timestamp("2007-02-14") + pd.to_timedelta(nc["time"][:], unit="s")
        idx = [t.get_loc(pd.Timestamp(f"2007-02-14 {h}")) for h in horas]
        return np.asarray(nc[nome][:])[idx]


@pytest.mark.parametrize("grandeza", list(VARIAVEL))
def test_grandezas_impressas(netcdf, grandeza):
    horas = [h for h, *_ in EXEMPLO]
    obtidos = _valores(netcdf, VARIAVEL[grandeza], horas)
    for (hora, _, impresso, _), obtido in zip(EXEMPLO, obtidos):
        esperado = impresso[grandeza]
        assert abs(obtido - esperado) <= _tol(esperado), \
            f"{grandeza} às {hora}: {obtido} x {esperado} impresso"


def test_densidade_por_classe(netcdf):
    obtidos = _valores(netcdf, "num_drop_density", [h for h, *_ in EXEMPLO])
    for (hora, _, _, nd), linha in zip(EXEMPLO, obtidos):
        for classe, esperado in nd.items():
            assert abs(linha[classe - 1] - esperado) <= _tol(esperado), (hora, classe)


def test_rain_amount_e_horario(netcdf):
    """RA = RI t / 3600 (apêndice 4.3) e o horário gravado é o INÍCIO do minuto
    (guia, seção 2.3.1): time_bounds = [19:47, 19:48]."""
    rr = _valores(netcdf, "rain_rate", ["19:47:00"])[0]
    assert abs(rr * 60 / 3600 - EXEMPLO[0][2]["RA"]) <= 5e-5
    tb = _valores(netcdf, "time_bounds", ["19:47:00"])[0]
    assert tb.tolist() == [19 * 3600 + 47 * 60, 19 * 3600 + 48 * 60]


def test_contagens_omitidas_sao_determinadas():
    """n11-n13 não estão impressas, mas RI, Wg, ZdB e EF (lineares nas contagens)
    as determinam: em 0..199 só um trio inteiro reproduz os quatro valores."""
    D = np.asarray(CONST["drop_mean_diam"], dtype=float)
    v = np.asarray(CONST["fall_vell"], dtype=float)
    F, t = float(CONST["sensor_area"]), 60.0
    pesos = {"RI": np.pi / 6 * 3.6e-3 / (F * t) * D**3,
             "Wg": np.pi / 6 / (F * t) / 1000 * D**3 / v,
             "Z": 1 / (F * t) * D**6 / v,
             "EF": np.pi / 12 / F * 1e-6 * D**3 * v**2 * 3600 / t}
    n11, n12 = np.meshgrid(np.arange(200.0), np.arange(200.0), indexing="ij")
    for _, n, imp, _ in EXEMPLO:
        n = np.asarray(n, dtype=float)
        fixas = np.r_[0:10, 13:20]
        base = {q: w[fixas] @ n[fixas] for q, w in pesos.items()}
        # RI fixa n13 (dado n11, n12); os outros três conferem
        n13 = np.round((imp["RI"] - base["RI"] - pesos["RI"][10] * n11
                        - pesos["RI"][11] * n12) / pesos["RI"][12])

        def total(q):
            return base[q] + pesos[q][10] * n11 + pesos[q][11] * n12 + pesos[q][12] * n13

        with np.errstate(invalid="ignore", divide="ignore"):
            ok = ((n13 >= 0) & (n13 < 200)
                  & (np.abs(total("RI") - imp["RI"]) <= 0.5e-4)
                  & (np.abs(total("Wg") - imp["Wg"]) <= 0.5 * 10 ** -_casas(imp["Wg"]))
                  & (np.abs(10 * np.log10(total("Z")) - imp["ZdB"]) <= 0.5 * 10 ** -_casas(imp["ZdB"]))
                  & (np.abs(total("EF") - imp["EF"]) <= 0.5 * 10 ** -_casas(imp["EF"])))
        achados = [(int(a), int(b), int(c)) for a, b, c in zip(n11[ok], n12[ok], n13[ok])]
        assert achados == [tuple(int(x) for x in n[10:13])]
