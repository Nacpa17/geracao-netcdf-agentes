"""Grade de tempo diária (UTC) e controle de qualidade do tempo (qc_time).

Cada observação bruta tem o horário de INÍCIO da integração. Ela vai para o
passo nominal mais próximo (empate de meio passo vai para frente). A grade do
dia tem 86400/passo posições; as que não recebem observação ficam vazias
(NaN) e com o bit "sem observação" no qc_time. Nada é interpolado nem somado:
se duas observações caem no mesmo passo, fica a primeira e o passo é marcado.

Bits do qc_time (mesma ideia do qc_time da ARM, mais o bit 4):
    1  intervalo para a observação anterior é zero (horário repetido)
    2  intervalo menor que o passo nominal
    4  intervalo maior que o passo nominal (havia lacuna antes)
    8  nenhuma observação neste passo
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

BIT_REPETIDO, BIT_MENOR, BIT_MAIOR, BIT_SEM_OBS = 1, 2, 4, 8
QC_TIME_BITS = [
    (BIT_REPETIDO, "duplicate_timestamp",
     "Delta time between current and previous samples is zero.", "Indeterminate"),
    (BIT_MENOR, "time_step_below_nominal",
     "Delta time between current and previous samples is less than the lower limit.",
     "Indeterminate"),
    (BIT_MAIOR, "time_step_above_nominal",
     "Delta time between current and previous samples is greater than the upper limit.",
     "Indeterminate"),
    (BIT_SEM_OBS, "no_observation",
     "No source observation assigned to this time step.", "Indeterminate"),
]


@dataclass
class Grade:
    """Um dia na grade nominal. É o que o plug-in recebe em `calcular`."""
    dia: pd.Timestamp                 # 00:00 UTC do dia
    passo_s: int
    tempos: pd.DatetimeIndex          # horários nominais (UTC)
    dados: pd.DataFrame               # colunas do `ler`, uma linha por passo
    presente: np.ndarray              # bool: o passo recebeu observação
    qc_time: np.ndarray               # int64, bits acima
    n_obs: int                        # observações do dia (antes de descartes)
    inicio_obs: pd.DatetimeIndex | None = None   # início real da observação
                                                 # escolhida (NaT sem observação)

    @property
    def segundos(self) -> np.ndarray:
        """Segundos desde 00:00 do dia (variáveis time e time_offset)."""
        return (self.tempos - self.dia).total_seconds().to_numpy(dtype=float)

    def limites(self, duracao=None) -> np.ndarray:
        """time_bounds (padrão ARM 1.3, seção 6.1.4), em segundos desde 00:00:
        [início observado, início + duração] de cada passo. `duracao` (s) vem
        do aparelho (ex.: o Interval do RD-80); sem ela, usa o passo nominal.
        Passo sem observação fica com o intervalo nominal [t, t + passo]: a
        ARM não aceita valor ausente em time_bounds."""
        inicio = self.segundos.copy()
        tem = np.zeros(len(self), dtype=bool)
        if self.inicio_obs is not None:
            obs = pd.DatetimeIndex(self.inicio_obs)
            tem = ~np.asarray(obs.isna())
            inicio[tem] = (obs[tem] - self.dia).total_seconds().to_numpy(dtype=float)
        dur = np.full(len(self), float(self.passo_s))
        if duracao is not None:
            d = np.asarray(duracao, dtype=float)
            ok = tem & np.isfinite(d) & (d > 0)
            dur[ok] = d[ok]
        return np.column_stack([inicio, inicio + dur])

    def __len__(self) -> int:
        return len(self.tempos)


def para_utc(indice: pd.Index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(indice)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx


def nominal(indice: pd.DatetimeIndex, passo_s: int) -> pd.DatetimeIndex:
    """Passo mais próximo; empate exato de meio passo vai para frente."""
    passo = pd.Timedelta(seconds=passo_s)
    return (indice + passo / 2).floor(f"{passo_s}s")


def preparar_observacoes(obs: pd.DataFrame, passo_s: int) -> pd.DataFrame:
    """Ordena (estável), põe em UTC e calcula os bits de tempo de cada
    observação em relação à anterior (considerando TODOS os arquivos lidos,
    inclusive dias vizinhos)."""
    if not isinstance(obs.index, pd.DatetimeIndex) and not len(obs) == 0:
        raise TypeError("`ler` deve devolver um DataFrame com índice de datas "
                        "(início de cada observação)")
    obs = obs.copy()
    obs.index = para_utc(obs.index)
    if obs.index.hasnans:
        raise ValueError("há observações sem horário (NaT) no índice")
    obs = obs.sort_index(kind="mergesort")
    # total_seconds (e não asi8/1e9): no pandas 3 a resolução do índice pode
    # ser s, ms, us ou ns, conforme a leitura
    dt = (obs.index[1:] - obs.index[:-1]).total_seconds().to_numpy(dtype=float) \
        if len(obs) > 1 else np.array([])
    bits = np.zeros(len(obs), dtype=np.int64)
    if len(obs) > 1:
        bits[1:][dt == 0] |= BIT_REPETIDO
        bits[1:][(dt > 0) & (dt < passo_s)] |= BIT_MENOR
        bits[1:][dt > passo_s] |= BIT_MAIOR
    obs["_qc_time"] = bits
    obs["_nominal"] = nominal(obs.index, passo_s)
    return obs


def dias_com_dados(obs: pd.DataFrame) -> list[pd.Timestamp]:
    """Dias (UTC) que têm pelo menos uma observação na grade nominal."""
    if len(obs) == 0:
        return []
    return sorted(pd.DatetimeIndex(obs["_nominal"]).normalize().unique())


def montar_grade(obs: pd.DataFrame, dia, passo_s: int) -> Grade:
    """`obs` deve vir de `preparar_observacoes`."""
    if 86400 % passo_s:
        raise ValueError(f"passo de {passo_s} s não divide o dia")
    dia = pd.Timestamp(dia).normalize()
    if dia.tz is not None:
        dia = dia.tz_convert("UTC").tz_localize(None)
    n = 86400 // passo_s
    tempos = pd.date_range(dia, periods=n, freq=f"{passo_s}s")
    do_dia = obs[(obs["_nominal"] >= dia) & (obs["_nominal"] < dia + pd.Timedelta(days=1))]
    posicao = ((do_dia["_nominal"] - dia).dt.total_seconds() // passo_s).astype(int).to_numpy()

    qc = np.full(n, BIT_SEM_OBS, dtype=np.int64)
    presente = np.zeros(n, dtype=bool)
    colunas = [c for c in obs.columns if c not in ("_qc_time", "_nominal")]
    dados = pd.DataFrame(index=tempos, columns=colunas, dtype=object)
    escolhidas = []
    for linha, pos in enumerate(posicao):
        bits = int(do_dia["_qc_time"].iat[linha])
        if presente[pos]:
            # outra observação no mesmo passo: fica a primeira e o passo
            # herda os bits dela (repetida ou intervalo menor que o passo)
            qc[pos] |= bits
            continue
        presente[pos] = True
        qc[pos] = bits
        escolhidas.append((pos, linha))
    inicio_obs = pd.Series(pd.NaT, index=tempos, dtype="datetime64[ns]")
    if escolhidas:
        pos_idx, lin_idx = map(list, zip(*escolhidas))
        bloco = do_dia.iloc[lin_idx][colunas]
        bloco.index = tempos[pos_idx]
        dados.loc[bloco.index, colunas] = bloco.to_numpy(dtype=object)
        inicio_obs.iloc[pos_idx] = pd.DatetimeIndex(do_dia.index[lin_idx]).as_unit("ns")
    dados = dados.infer_objects()
    for c in colunas:          # mantém o tipo das colunas do `ler`
        if pd.api.types.is_numeric_dtype(obs[c]):
            dados[c] = pd.to_numeric(dados[c], errors="coerce")
    return Grade(dia=dia, passo_s=passo_s, tempos=tempos, dados=dados,
                 presente=presente, qc_time=qc, n_obs=len(do_dia),
                 inicio_obs=pd.DatetimeIndex(inicio_obs.to_numpy()))
