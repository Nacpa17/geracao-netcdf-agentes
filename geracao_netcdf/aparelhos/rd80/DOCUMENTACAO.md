# RD-80 (disdrômetro Joss-Waldvogel) — processamento

Especificação do que o plug-in `rd80` calcula, variável por variável, com a
fonte de cada regra. Serve para conferir a física e é o "manual" que o
agente recebe no modo `do_zero` (quando não vê o código do plug-in).

Fontes: **[M1]** DISTROMET, *RD-80 Operating Instructions* (2015);
**[M2]** DISTROMET, *User Guide for DISDRODATA 2.0* (2009); **[M3]**
DISTROMET, *Data Processing* (joss.pdf); **[ARM]** Bartholomew, *Impact
Disdrometer Instrument Handbook*, DOE/SC-ARM-TR-111 (2016); **[STD]** ARM
Data File Standards 1.3, DOE/SC-ARM-15-004 (2020); **[DM]** manual de gestão
de dados do RD-80 no ATTO (v5, 2026).

## Aparelho e arquivo bruto

- Disdrômetro de impacto RD-80: o processador manda um código (127 tamanhos)
  por gota; o DISDRODATA agrupa em 20 classes de 0,3 a 5 mm e integra 60 s
  [M2 2.2, M3]. Área de amostragem 50 cm² = 0,005 m²; diâmetro ±5 % [M1].
  A velocidade de queda é tabelada (Gunn & Kinzer, 1949), não medida [M2 4.2].
- Arquivo `RD-YYMMDD-hhmmss.txt` [M2 4.4.1]: TAB, fim de linha CRLF,
  vírgula decimal (no ATTO), uma linha de cabeçalho:
  `YYYY-MM-DD, hh:mm:ss, Status, Interval [s], n1..n20, RI [mm/h], RA [mm], RAT [mm]`.
- Horário = **início** do intervalo: "A time stamp marks always the
  beginning of an interval" [M2 2.3.1]. No ATTO o relógio está em UTC [DM].
- `RI` (taxa), `RA` (chuva no intervalo, `RI·t/3600`) e `RAT` (acumulado
  desde o início da gravação — não é total diário) vêm do próprio
  software [M2 4.3]; servem de conferência: `RI` deve bater com `rain_rate`.

## Constantes (`variables_info.json`)

| chave | significado |
|---|---|
| `drop_mean_diam` | diâmetro médio D_i de cada classe (mm) [M2 4.2] |
| `delta_diam` | largura dD_i de cada classe (mm) [M2 4.2] |
| `fall_vell` | velocidade de queda v_i de cada classe (m/s) [M2 4.2] |
| `sensor_area` | área F do sensor (m²) [M1, M2 4.3] |
| `filter_ni` | limite de contagem baixa (gotas no intervalo) para o bit 2 do QC [DM] |
| `calibration_date` | datas de calibração (aaaa-mm-dd) |
| `Columns`, `drop_col` | nomes canônicos das colunas (as de contagem são `n1`..`n20`) |
| `integration_time` | passo nominal da grade (s) |

## Regras gerais

- Cada passo da grade tem as contagens `n_i` e o intervalo `dt` (coluna
  `Interval`, em s). Passos sem observação vêm com NaN.
- Contagens **válidas**: as 20 são finitas e ≥ 0. Intervalo **válido**:
  finito e > 0. Só passos com contagens e intervalo válidos entram nas
  contas; os demais dão NaN (o núcleo grava o `missing_value`).
- Minuto seco (todas as contagens zero) é válido: taxa, água líquida e
  energia dão 0; o que dependeria de dividir por zero dá NaN [DM].

## Variáveis

Fórmulas do apêndice 4.3 de [M2], com `k = 1 / (F · dt)` e somas sobre as
20 classes:

| variável | dimensões | cálculo |
|---|---|---|
| `drop_avg_class` | classe | `drop_mean_diam` (coordenada) |
| `fall_velocity` | classe | `fall_vell` |
| `delta_diam` | classe | `delta_diam` |
| `num_drop` | tempo × classe | contagens brutas `n_i` |
| `number_detected_drops` | tempo | `Σ n_i` |
| `diam_max` | tempo | D_i da **maior** classe com `n_i > 0` ("Dmax"); NaN sem gotas |
| `diam_min` | tempo | D_i da **menor** classe com `n_i > 0`; NaN sem gotas |
| `num_drop_density` | tempo × classe | `N(D_i) = n_i / (F · dt · v_i · dD_i)`  [1/(m³ mm)] |
| `rain_rate` | tempo | `RI = (π/6) · 3,6·10⁻³ · k · Σ n_i D_i³`  [mm/h] |
| `liq_water` | tempo | `Wg = (π/6) · k · Σ (n_i / v_i) D_i³ / 1000`  [g/m³] |
| `radar_reflectivity` | tempo | `ZdB = 10 · log10(Z)`, `Z = k · Σ (n_i / v_i) D_i⁶`  [mm⁶/m³ → dBZ]; NaN se Z ≤ 0 |
| `energy_flux` | tempo | `EK = (π/12) · (1/F) · 10⁻⁶ · Σ n_i D_i³ v_i²` [J/m²]; `EF = EK · 3600 / dt`  [J/(m² h)] |
| `slope_parameter` | tempo | `Λ = (6!/π · W / Z)^(1/3)`, com `W = 1000 · Wg` (mm³/m³)  [1/mm] |
| `distribution_intercept` | tempo | `N₀ = (1/π) · (6!/π)^(4/3) · W · (W / Z)^(4/3)`  [1/(m³ mm)] |
| `sample_interval` | tempo | `dt` como veio do arquivo |
| `time_bounds` | tempo × 2 | `[início observado, início + dt]` em segundos desde 00:00 (as units de `time`); sem observação ou com `dt` inválido: `[time, time + passo]` [STD 6.1.4] |

`slope_parameter` e `distribution_intercept` (exponencial ajustada pelos
momentos 3 e 6) só existem quando `Z > 0` e `W > 0`; senão NaN.

## Controle de qualidade

`qc_number_detected_particles` (bits somados) [DM]:

| valor | quando |
|---|---|
| 1 | `Σ n_i == 0` (sem gotas: informativo, não é falha) |
| 2 | `0 < Σ n_i < filter_ni` |
| 4 | contagens inválidas (alguma ausente, não finita ou negativa) |
| 8 | intervalo inválido (ausente, não finito ou ≤ 0) |

Passo sem observação: contagens e intervalo ausentes → 4 + 8 = 12.

`qc_sample_interval` (mesmos bits do RD80-Processing 3.1) [DM]:

| valor | quando |
|---|---|
| 1 | passo com observação e `dt` ausente, não finito ou ≤ 0 |
| 2 | `0 < dt < passo nominal` |
| 4 | `dt > passo nominal` |
| 8, 16 | não usados (agregação de registros, que este gerador não faz) |
| 32 | passo sem observação |

`qc_calibration` (indicador de estado, não teste de qualidade [STD 6.5.2]):
para cada data de calibração, em ordem, o bit `2^i` fica ligado nos passos
**com observação** cujo início observado é ≥ àquela data; 0 nos demais.

O núcleo cria `base_time`, `time_offset`, `time` e `qc_time` (e um
`time_bounds` nominal, que o plug-in refina); `lat`, `lon` e `alt` vêm
fixos do esquema.

## Cuidados conhecidos (não corrigidos automaticamente)

- **Teste do processador**: o botão TEST gera um trem de pulsos de 1000 Hz
  [M1 8]; na ARM aparece como "algumas centenas" de gotas na classe 7
  (~1,1 mm) com pouco ou nada nas outras, uma vez por semana em tempo seco
  — esses minutos devem ser ignorados [ARM 8.0].
- **Vento e ruído acústico** fazem o sensor vibrar: gotas falsas na menor
  classe (~0,3 mm) [ARM 8.0, M1]; ruído alto também esconde gotas pequenas
  [M1].
- Sem correção de tempo morto ou de coincidência (chuva intensa) [DM].
- Conferência de campo do fabricante: chuva contínua de 1–10 mm/h por
  várias horas, vento fraco, pluviômetro de referência; os totais do evento
  devem concordar em ~15 % [M1 6.3, ARM 5.0].

## Validação

- Reproduz o **exemplo de saída do próprio software** do fabricante [M2
  4.4–4.5] até a última casa impressa (RI, Dmax, Wg, ZdB, EF, N₀, Λ e
  N(Dᵢ)); ver `tests/test_geracao_rd80_fabricante.py`.
- Aplicadas aos dados da ARM do mesmo tipo de instrumento, as fórmulas
  reproduzem as variáveis publicadas pela ARM até o arredondamento (4ª casa).
- Nos dados do ATTO (15–18/12/2024), as 25 variáveis em comum com o
  RD80-Processing 3.1 saem idênticas, e os `time_bounds` também (nos passos
  com dado; lá eles estão em segundos desde 1970).
