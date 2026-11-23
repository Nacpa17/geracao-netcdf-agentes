# Geração de NetCDF com agentes: da consulta do dado à geração dele

> Proposta do orientador para esta fase da IC (nov/2026): depois de aprender a
> consultar os metadados e extrair essas informações, gerar o arquivo do zero,
> agora com agentes. Isso fecha o ciclo: da consulta do dado à geração dele.

Na prática: hoje, cada aparelho novo (ou cada formato novo de um aparelho já
conhecido) exige escrever à mão um script que lê o arquivo bruto e grava o
NetCDF com os metadados certos. Este repositório transforma isso em três peças:

1. um **núcleo comum** que grava o NetCDF no padrão ARM/CF a partir de um
   esquema de metadados (o mesmo `netCDF_info.json` do ATTO);
2. um **plug-in por aparelho**, com só três funções (ler, dimensões, calcular);
3. um **agente** — um modelo de linguagem num laço com o validador — que
   escreve o plug-in de um formato/aparelho novo e só para quando o arquivo
   gerado está certo.

Os metadados que o projeto aprendeu a consultar nas fases anteriores da IC
([repositório](https://github.com/Nacpa17/assistente-metadados-ambientais)),
DODs e arquivos da ARM, entram como **molde do esquema** de um aparelho novo.

```
 NetCDF/DOD da ARM ──molde──► esquema (netCDF_info.json) + constantes
                                         │
 arquivos brutos + descrição ──► agente ──► plug-in (ler, dimensoes, calcular)
                                   ▲              │
                                   │              ▼
                              retorno ◄── validador ◄── núcleo ──► NetCDF diário
```

## 1. O núcleo e o plug-in

Tudo fica em `geracao_netcdf/`:

| módulo | o que faz |
|---|---|
| `esquema.py` | lê o `netCDF_info.json` do ATTO (variáveis, tipos, dimensões, atributos, globais) e o `variables_info.json` (constantes do aparelho) |
| `grade.py` | grade diária UTC no passo nominal, sem interpolar; `qc_time` com 4 bits (repetido, intervalo menor, intervalo maior, sem observação); `time_bounds` com o início observado |
| `escrita.py` | grava o NetCDF: atributos tipados como a variável, sem `_FillValue`, `Conventions`, `history`/`command_line`/`input_source` reais, `input_sha256` dos brutos, escrita atômica |
| `gerar.py` | CLI: plug-in + esquema → um NetCDF por dia **com dado** |
| `validador.py` | CLI: confere qualquer NetCDF (com ou sem esquema, com ou sem gabarito) |
| `padrao_arm.py` | perfil `--padrao arm` do validador: checklist do ARM Data File Standards 1.3 |
| `aparelhos/rd80/` | plug-in do RD-80 + esquema + constantes + `DOCUMENTACAO.md` (fórmulas e fontes) |
| `aparelhos/parsivel2/` | física do Parsivel² conferida com a ARM + `DOCUMENTACAO.md` (ainda sem `ler`) |
| `molde.py` | esquema de um aparelho novo a partir de um NetCDF de exemplo ou do DOD da ARM |
| `agente.py`, `llm.py` | o agente e os clientes de modelo (local no Colab ou por API) |
| `sintetico.py`, `bench.py` | dado sintético em 7 formatos e o benchmark do agente |

O contrato do plug-in (`geracao_netcdf/aparelhos/__init__.py`):

```python
def ler(arquivos, const) -> pandas.DataFrame     # índice = início de cada observação, UTC
def dimensoes(const) -> dict[str, numpy.ndarray]  # ex.: {"drop_avg_class": diâmetros}
def calcular(grade, const) -> dict[str, numpy.ndarray]   # variáveis de UM dia
```

O núcleo cuida de ordenar, marcar repetidos e lacunas, montar a grade,
criar `base_time`/`time_offset`/`time`/`qc_time`, trocar NaN pelo
`missing_value` e gravar os metadados. O plug-in só sabe do aparelho.

### Converter o RD-80

```bash
python -m geracao_netcdf.gerar --aparelho rd80 --entrada pasta_dos_txt --saida saida/rd80 --validar
python -m geracao_netcdf.gerar --aparelho rd80 --entrada pasta_dos_txt --saida saida/rd80 --dia 16/12/2024
```

### Conferência com o RD80-Processing 3.1 do grupo

Rodado sobre os 3 arquivos do ATTO que vieram no `RD80-Processing` (15 a
18/12/2024), comparando com os NetCDFs que o próprio pacote traz:

| | RD80-Processing 3.1 | `geracao_netcdf` |
|---|---|---|
| variáveis | 58 (inclui trilha `observation_*` e 13 `qc_*_summary`) | 26 (as 19 do catálogo do ATTO + QC + `time_bounds`) |
| valores das 25 variáveis em comum | — | **idênticos** (bit a bit), com pandas 2.3 e 3.1 |
| `time_bounds` | em segundos desde 1970, **sem units** e com -9999 nos minutos sem dado | iguais nos minutos com dado; relativos a `time:units` e sem ausentes, como pedem a ARM (6.1.4) e o CF (7.1) |
| tempo para os 4 dias | ~30 s | ~1 s |
| roda com pandas 3 / xarray novo | não (`date_parser` removido; NaN no lugar de -9999) | sim |
| validador | reprovado no próprio esquema: 9 erros, 75 avisos (com `--padrao arm`: 31 erros) | aprovado no esquema do RD-80: nota 100 (com `--padrao arm`: 0 erros, 11 avisos) |

Sobre os `time_bounds` do 3.1: sem `units`, a regra CF/ARM manda ler os
valores na referência de `time` (segundos desde a meia-noite), então um
leitor CF entende 1,7 bilhão de segundos depois da meia-noite. O validador
acusa isso (`E_TEMPO_LIMITES`).

Os 9 erros do arquivo do RD80-Processing, no próprio esquema dele:
`drop_avg_class`, `bound` e `observation` sem `long_name`/`units`;
`qc_calibration` com `valid_max = "N/A"` (texto numa variável inteira); o
global obrigatório `averaging_interval` ausente; os `time_bounds` em outra
referência (acima). Os avisos: campos internos
do catálogo (`Variable ID`, `Shortname`...) gravados como atributo em 55
variáveis, 13 `qc_*_summary` sem descrição dos bits, `conventions` em
minúsculas e unidades escritas de dois jeitos (`mm h-1`/`mm/h`,
`1/(m^3 mm)`/`1/(m^3-mm)`, `1`/`unitless`).

### Conferência com a documentação do fabricante (documentos do Joss)

- **Exemplo de saída do software do fabricante.** O guia do DISDRODATA 2.0
  (apêndices 4.4 e 4.5) imprime três minutos de exemplo com as contagens e o
  que o software calcula. O plug-in reproduz **todos** os valores impressos
  até a última casa — RI, Dmax, Wg, ZdB, EF, N₀, Λ e N(Dᵢ)
  (`tests/test_geracao_rd80_fabricante.py`). O guia não imprime as contagens
  das classes 11 a 13; elas são o único trio inteiro que reproduz RI, Wg, ZdB
  e EF ao mesmo tempo. Duas ressalvas do próprio guia: o N₀ do 2º minuto
  difere na 7ª casa significativa (1212,97163 × 1212,9717), e a coluna
  impressa como "N(d7)" não corresponde a classe nenhuma (erro de
  diagramação do guia).
- **Horário.** "A time stamp marks always the beginning of an interval"
  (guia, seção 2.3.1): confirma a convenção de início usada pelo grupo e
  pelo plug-in; agora o intervalo real vai em `time_bounds`.
- **Cuidados do aparelho** que entraram na documentação do plug-in: o botão
  TEST do processador gera centenas de "gotas" na classe 7 (a ARM manda
  ignorar esses minutos), vento e ruído geram gotas falsas na menor classe,
  e não há correção de tempo morto. Ficaram como alerta; não há detecção
  automática (é um item em aberto também no manual de gestão de dados).
- Antes disso, aplicada aos dados da ARM do mesmo tipo de instrumento, a
  física já reproduzia as variáveis publicadas pela ARM até o arredondamento,
  e nos dados do ATTO a taxa calculada bate com o `RI` do aparelho.

### Metadados no padrão ARM 1.3

O esquema do RD-80 é derivado do JSON do grupo por
`scripts/limpar_esquema_rd80.py`, que também alinha os metadados ao *ARM
Data File Standards 1.3* (DOE/SC-ARM-15-004): `time_bounds` e
`time:bounds`, `standard_name` em `time`, `lat`, `lon` e `alt`,
`base_time` ↔ `time_offset`, coordenadas sem `missing_value`, unidade "1"
no lugar de "unitless", QC com `long_name`, `description`, `flag_method` e
`standard_name` padrão (bits que o gerador não usa viram "Not used"),
`qc_calibration` como indicador de estado, `location_description`. O
`qc_sample_interval` do 3.1 entrou com os mesmos bits.

O que continua fora do padrão ARM é decisão do grupo, e o validador com
`--padrao arm` lista: `data_level = "ARM b1"` (a ARM usa só "b1"), `doi`
como URL (a ARM guarda o número e a URL em `doi_url`), QC em inteiro de 64
bits e NetCDF4 (a ARM recomenda 32 bits, que permitiria netCDF-3), o nome
`qc_number_detected_particles` (a variável de dado é `number_detected_drops`),
e `title`/`doi_url` ausentes.

## 2. O validador

```bash
python -m geracao_netcdf.validador saida/rd80/*.nc --aparelho rd80
python -m geracao_netcdf.validador arquivo.nc --esquema netCDF_info.json --referencia pasta_gabarito --json rel.json
python -m geracao_netcdf.validador arquivo.nc --aparelho rd80 --padrao arm      # + checklist ARM 1.3
```

Nota = 100 − 15 × erros − 3 × avisos. **Aprovado = nenhum erro.** O código
de saída é 1 se algum arquivo for reprovado (dá para usar em script).

| código | o que pega |
|---|---|
| `E_VAR_FALTANDO`, `E_VAR_PROIBIDA`, `A_VAR_EXTRA` | variáveis x esquema |
| `E_DIMS`, `E_TIPO` | dimensões e tipo de cada variável |
| `E_ATTR_FALTANDO` | `long_name`/`units` (regra da ARM; variáveis de limites CF e de texto ficam isentas) |
| `E_ATTR_TIPO` | `missing_value`, `valid_min/max`, `flag_masks`… com tipo diferente da variável ou em texto |
| `E_ANCILLARY` | `ancillary_variables`/`bounds` citando variável que não existe |
| `E_QC_BITS`, `A_QC_SEM_DESCRICAO` | valor de QC usando bit não descrito (na variável ou nos `qc_bit_N_description` globais, como a ARM faz) |
| `E_NAN`, `E_FAIXA` | NaN onde deveria haver `missing_value`; valores fora de `valid_min/max` |
| `E_TEMPO` | tempo ausente/NaN ou fora de ordem, `units` sem data, `base_time + time_offset ≠ time` |
| `E_TEMPO_LIMITES`, `A_TEMPO_LIMITES` | `time_bounds` com ausentes, com units próprias, em outra referência ou sem conter o `time` |
| `E_VAZIO` | arquivo sem nenhuma observação |
| `E_GLOBAL_FALTANDO`, `E_GLOBAL_PLACEHOLDER` | globais obrigatórios; `__AUTO__`/`TBD` que sobraram |
| `A_CONVENTIONS`, `A_HISTORY`, `A_GLOBAL_SEM_UNIDADE`, `A_NOME_ARQUIVO`, `A_UNIDADES_MISTAS`, `A_ATTR_INTERNO` | boas práticas CF/ARM |
| `R_DIFERENCA`, `R_FORMA`, `R_FALTANDO` | comparação com um NetCDF-gabarito, variável por variável |

Sem falso positivo nos arquivos de verdade da ARM: os 5 exemplos que vieram
com o material (disdrômetro de SGP e TWP, Parsivel² de SGP) passam sem erro;
os mais antigos só recebem o aviso de não ter `Conventions`.

**Perfil `--padrao arm`** (`geracao_netcdf/padrao_arm.py`): acrescenta o
checklist do *ARM Data File Standards 1.3*, citando a seção em cada
mensagem — formato e nome do arquivo (4.0, 5.1), dimensão de tempo,
`base_time`/`time_offset`/`time` e `time_bounds` (6.1), coordenadas (6.2),
`lat`/`lon`/`alt` (6.3), `long_name` único e "1" no lugar de "unitless"
(6.6), globais obrigatórios e `datastream = site + plataforma + facility +
"." + nível` (6.7) e as regras de QC (6.8). `ARM_E_*` é o que o padrão exige;
`ARM_A_*`, o que recomenda. Calibrado com os arquivos da ARM: o Parsivel² de
2023 (ARM-1.2) passa sem erro, só com avisos da redação antiga ("on field:");
os do RD-80 da ARM, de padrão mais antigo, não passam.

## 3. Esquema de um aparelho novo: o molde

```bash
# a partir de um NetCDF de exemplo da ARM (ex.: sgpldE13.b1, Parsivel² da ARM)
python -m geracao_netcdf.molde --de-netcdf sgpldE13.b1.20230828.000000.cdf --saida aparelhos_novos/parsivel2 \
    --datastream attldCA.b1 --site att --facility CA --lat -2.1815896 --lon -59.0218639 --alt 59
# a partir do DOD oficial (pela rede, com cache local em data/arm_extra/cache)
python -m geracao_netcdf.molde --de-dod ld.b1 --saida aparelhos_novos/parsivel2
```

Sai `netCDF_info.json` (variáveis, tipos, dimensões, atributos e globais;
`command_line`, `history`... como `__AUTO__`), `variables_info.json` (as
coordenadas que não dependem do tempo — diâmetros e velocidades das classes
— viram constantes) e um `tarefa.yaml` para o agente. O que é do local ou do
aparelho fica listado em `"_revisar"` no esquema.

Conferência: o molde tirado do Parsivel² da ARM (45 variáveis) aprova os
próprios arquivos da ARM com nota 100, e o tirado de um arquivo do RD-80
serve para o gerador produzir arquivos válidos de novo (testes em
`tests/test_geracao_molde.py`).

### Parsivel² do ATTO: o que já está pronto

`aparelhos_novos/parsivel2/` tem o esquema e as constantes para o ATTO
(molde do `sgpldE13.b1`, com o sítio do ATTO-Campina) e a tarefa do agente;
`geracao_netcdf/aparelhos/parsivel2/fisica.py` tem a física, conferida com o
ingest da ARM (`scripts/conferir_parsivel_arm.py`): `number_density_drops`,
conteúdo de água, momentos e refletividade reproduzidos com erro relativo
de ~1e-7, diâmetros extremos exatos, parâmetros da exponencial a 0,2 %. A
conferência achou três armadilhas nos arquivos da ARM, já corrigidas no
esquema do ATTO:

1. o `raw_spectrum` está guardado como [tempo, velocidade, tamanho], embora
   as dimensões declaradas sejam (time, particle_size, raw_fall_velocity) —
   na ordem declarada, gotas de 3 mm cairiam a 0,6 m/s;
2. `fall_velocity_calculated` é por classe de tamanho, não de velocidade;
3. `effective_measurement_area = "0.54 m^2"`, mas o cálculo da própria ARM
   usa 0,0054 m² (54 cm², a área nominal do manual TR-137).

Falta um arquivo bruto do Parsivel² do ATTO para o agente escrever o `ler`.

## 4. O agente

```bash
python -m geracao_netcdf.agente --tarefa aparelhos_novos/parsivel2/tarefa.yaml --llm hf:Qwen/Qwen2.5-Coder-7B-Instruct
```

A tarefa (`tarefa.yaml`) diz: descrição do aparelho e do **formato** do
arquivo (separador, decimal, fuso, se o horário é de início ou de fim),
1–3 arquivos de amostra, esquema, constantes e, opcionalmente, o manual com
as fórmulas, um plug-in parecido como exemplo e **pontos de controle**.

Cada tentativa passa por cinco etapas; a primeira que falha vira o retorno
para o modelo:

1. **extração** — achar o bloco de código na resposta;
2. **checagem estática** — imports permitidos (numpy, pandas, biblioteca
   padrão de leitura e os plug-ins já validados), nada de gravar/apagar
   arquivos, `eval`, `os`, `subprocess`; as três funções do contrato;
3. **execução** — roda o gerador num subprocesso, com tempo limite e sem as
   variáveis de ambiente com chaves; o retorno é o fim do traceback;
4. **validação** — o validador sobre cada NetCDF gerado (erros + um resumo
   mín/máx/% ausente de cada variável);
5. **pontos de controle** — valores conhecidos (ex.: a taxa de chuva que o
   aparelho mostrou num minuto). Se não batem, o agente procura o valor
   esperado em outro horário e diz o deslocamento ("aparece 4 h antes deste
   horário") — é assim que fuso e início × fim do intervalo aparecem.

Tudo fica registrado na pasta da rodada: `prompt.md`, `resposta_N.md`,
`plugin_N.py`, `retorno_N.md`, `saida_N/` e `relatorio.json`.

**Modelos** (opção `--llm`): `hf:<modelo>` roda local (Colab com GPU; numa
T4 use `--4bit`); `openai:<modelo>[@url]` serve OpenAI e servidores
compatíveis (vLLM, Ollama, LM Studio); `anthropic:<modelo>` usa a API da
Anthropic; `roteiro:<pasta>` usa respostas gravadas (testes). Chaves só por
variável de ambiente.

**Segurança:** o código do modelo é executado. A checagem estática e o
subprocesso barram o óbvio, mas não são uma sandbox — rode em ambiente
descartável (o Colab serve).

## 5. O benchmark

```bash
python -m geracao_netcdf.bench --llm roteiro:benchmarks/geracao/solucoes          # circuito: 7/7
python -m geracao_netcdf.bench --llm hf:Qwen/Qwen2.5-Coder-7B-Instruct --modo com_exemplo
python -m geracao_netcdf.bench --llm hf:Qwen/Qwen2.5-Coder-7B-Instruct --modo do_zero
```

O mesmo dado sintético do RD-80 (chuva simulada por Marshall-Palmer +
Poisson, com garoa, uma lacuna de 9 min e um registro repetido) é escrito
em 7 formatos de arquivo: `original` (DISDRODATA), `ponto_e_virgula`,
`iso_utc` (outros nomes de coluna), `hora_local` (UTC−4),
`fim_do_intervalo`, `colunas_trocadas` e `cabecalho_longo`. O gabarito é o
formato original lido pelo plug-in validado; um plug-in correto para
qualquer formato tem de gerar NetCDFs **idênticos** a ele.

- `com_exemplo`: o prompt traz o código do plug-in do RD-80 (que lê outro
  formato) — mede **adaptar o leitor**, o caso mais comum;
- `do_zero`: só a documentação com as fórmulas — mede **escrever tudo**.

Métricas por formato: correto (todos os dias, todas as variáveis iguais ao
gabarito, sem erro de esquema), tentativas, etapa final, variáveis iguais,
tempo do modelo. No geral: taxa de acerto, acertos na 1ª tentativa, média
de tentativas.

**Resultado com respostas gravadas** (confere o circuito, não um modelo):
7/7, seis na 1ª tentativa; em `hora_local` a 1ª resposta esquece o fuso de
propósito, os pontos de controle reprovam ("aparece 4 h antes") e a 2ª
corrige. **Achado:** sem os pontos de controle, o arquivo 4 h deslocado
**passa** no validador de esquema e só o gabarito pega o erro
(`tests/test_geracao_bench.py`). Ou seja, validar metadados não basta para
validar o dado: o agente precisa de alguns valores conhecidos.

**Falta:** rodar com modelos de verdade (notebook `notebooks/IC_agente_colab.ipynb`,
pronto para "Executar tudo") e comparar modelos (ex.: Qwen2.5-Coder 7B × 14B
× um modelo por API) e os dois modos.

## 6. Próximos passos

1. Rodar o benchmark com modelos de verdade e montar a tabela da comparação.
2. Parsivel² do ATTO: esquema, constantes e física prontos
   (`aparelhos_novos/parsivel2/`); falta **um arquivo bruto** do Parsivel²
   do ATTO (e o formato do logger) para o agente escrever o `ler`.
3. Revisar com o grupo o esquema do RD-80 (26 variáveis): se a trilha de
   auditoria temporal (`observation_*`) do RD80-Processing deve voltar, ela
   entra como variáveis opcionais do esquema, sem mexer no núcleo.
4. Itens em aberto do manual de gestão de dados que agora têm documentação
   primária para decidir: detecção do teste do processador (classe 7) e de
   ruído de vento, correção de tempo morto, `data_level`/`doi` no formato da
   ARM e QC em 32 bits.
