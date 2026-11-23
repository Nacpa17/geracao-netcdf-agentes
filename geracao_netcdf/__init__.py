"""geracao_netcdf: do dado bruto ao NetCDF padronizado (ARM/CF) — e um agente
que escreve a parte de cada aparelho.

Cada aparelho (disdrômetro, pluviômetro, ...) tem um formato bruto próprio,
mas o NetCDF de saída segue sempre o mesmo esquema de metadados (o JSON no
formato do ATTO: netCDF_info.json + variables_info.json). Por isso o código
é dividido em:

- **núcleo comum**: lê o esquema (esquema.py), monta a grade de tempo diária
  com o controle de qualidade de tempo (grade.py), grava o NetCDF com
  metadados CF/ARM corretos (escrita.py, gerar.py) e valida o resultado
  (validador.py);
- **plug-in do aparelho** (aparelhos/<nome>/): só três funções — `ler`
  (formato bruto), `dimensoes` e `calcular` (variáveis derivadas);
- **molde** (molde.py): esquema de um aparelho novo a partir de um NetCDF de
  exemplo ou do DOD da ARM;
- **agente** (agente.py + llm.py): um modelo de linguagem escreve o plug-in
  de um aparelho/formato novo e corrige até o validador aprovar;
- **benchmark** (sintetico.py + bench.py): o mesmo dado em 7 formatos, com
  gabarito, para medir o agente.

Uso rápido (detalhes em docs/geracao_netcdf.md):
    python -m geracao_netcdf.gerar --aparelho rd80 --entrada pasta_dos_txt --saida saida/rd80
    python -m geracao_netcdf.validador saida/rd80/*.nc --aparelho rd80
    python -m geracao_netcdf.bench --llm roteiro:benchmarks/geracao/solucoes
"""

__version__ = "1.0.0"
VERSAO = f"geracao-{__version__}"   # vai para o atributo process_version dos NetCDFs
