# Parsivel² (OTT, disdrômetro óptico) — preparo do plug-in

Ainda não é um aparelho registrado: falta o `ler` do formato bruto do ATTO
(o agente escreve quando houver uma amostra). O esquema e as constantes
para o ATTO já estão em `aparelhos_novos/parsivel2/` (molde tirado do
`sgpldE13.b1` da ARM) e a física, conferida com a ARM, em `fisica.py`.

Fontes: **[LD]** Zhu & Wang, *Laser Disdrometer (LDIS) Instrument Handbook*,
DOE/SC-ARM-TR-137 (2025); **[TR275]** Wang et al., *Analysis of Three Types
of Collocated Disdrometer Measurements at the ARM SGP*, DOE/SC-ARM-TR-275
(2021); **[ARMnc]** arquivos `sgpldE13.b1.20230828/29` (DOD ld-b1-2.0).

## Aparelho

- Extinção de um feixe laser: a atenuação dá o tamanho e a duração dá a
  velocidade de cada partícula [LD 1.1].
- 32 classes de tamanho (0,062 a 24,5 mm) × 32 de velocidade (0,05 a
  20,8 m/s), com espaçamento desigual; as classes 1 e 2 de tamanho ficam
  fora da faixa de medida e não são avaliadas [LD tabela 1].
- Área nominal 54 cm². Trabalhos da ARM usam uma área efetiva
  180 × (30 − D/2) mm² para o efeito de borda [LD 1.1] — mas o ingest b1 da
  ARM usa a área nominal (abaixo).
- Partículas coincidentes no feixe contam como uma só: subestima contagens
  em chuva forte [LD 6.0].

## O que o ingest da ARM (ld.b1) calcula — conferido

`scripts/conferir_parsivel_arm.py` recalcula, a partir do `raw_spectrum`,
o que a ARM publica nos dois arquivos de exemplo:

| variável | fórmula | erro relativo máximo |
|---|---|---|
| `number_density_drops` | `N(D_i) = Σ_j c[j,i] / (A · dt · v(D_i) · dD_i)`, A = 0,0054 m², dt = 60 s, v = `fall_velocity_calculated` | 8e-8 |
| `liquid_water_content` | `(π/6) · M3` [mm³/m³] | 2e-7 |
| `momentk` | `Mk = Σ N(D_i) D_i^k dD_i` | 1e-7 |
| `equivalent_radar_reflectivity` | `10 log10(M6)` | 5e-8 |
| `diameter_min`, `diameter_max` | menor/maior classe com N > 0 | 0 |
| `slope_parameter`, `intercept_parameter`, `median_volume_diameter`, `liquid_water_distribution_mean` | exponencial pelos momentos 3 e 6: `Λ = (120 M3/M6)^(1/3)`, `N0 = M3 Λ⁴/6`, `MVD = 3,67/Λ`, `Dm = 4/Λ` | 2e-3 (constante ligeiramente diferente na ARM) |

`c[j,i]` é a contagem na classe de **velocidade** j e de **tamanho** i.
`precip_rate`, `weather_code`, `mor_visibility`,
`equivalent_radar_reflectivity_ott` etc. vêm prontos do telegrama do
aparelho, não são recalculados.

## Armadilhas encontradas nos arquivos da ARM

1. **Ordem dos eixos do `raw_spectrum`.** As dimensões declaradas são
   `(time, particle_size, raw_fall_velocity)`, mas os dados estão como
   `[tempo, velocidade, tamanho]`. Na ordem declarada, gotas de 3 mm cairiam
   a 0,6 m/s; na ordem real, a velocidade média de cada classe acompanha a
   curva de Lhermitte, e só assim o `number_density_drops` da própria ARM é
   reproduzido. No esquema do ATTO as dimensões foram declaradas na ordem
   real: `(time, raw_fall_velocity, particle_size)`.
2. **`fall_velocity_calculated`** é por classe de tamanho, embora declarada
   na dimensão `raw_fall_velocity`.
3. **`effective_measurement_area = "0.54 m^2"`** no arquivo, mas o cálculo
   usa 0,0054 m² (54 cm²). No esquema do ATTO ficou "0.0054 m^2".

## Controle de qualidade

O esquema (molde da ARM) traz `qc_*` com os testes padrão da ARM: valor
ausente, abaixo de `valid_min`, acima de `valid_max` (bits 1, 2, 3). A ARM
ainda filtra, só no produto c1 (LDQUANTS), partículas com velocidade fora de
±50 % da relação de Tokay et al. (2013) [LD 6.0] — não é feito no b1.

## Comparação com o RD-80

Em 18 meses de dados colocalizados na SGP [TR275]: os dois concordam bem
para gotas médias (1–4 mm); o RD-80 subamostra gotas pequenas (0,5–1 mm),
sobretudo com R > 10 mm/h, e põe tudo acima de 5,4 mm na última classe; o
Parsivel² subamostra gotas < 0,56 mm e superestima as > 4 mm (R e Z mais
altos). Entre pares de aparelhos, a taxa de chuva em 5 min ficou com
|viés| < 1,2 mm/h e |viés| fracionário < 28 %. O plano de validação do
ATTO (manual de gestão de dados do RD-80) pede comparar quantidades físicas
em janelas de tempo iguais e espectros em faixas de diâmetro comuns —
nunca contagens brutas como se as áreas fossem iguais.
