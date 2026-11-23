# Histórico de versões

O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/), e as versões seguem o [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [1.0.0] - 2026-11-23

Primeira versão neste repositório. Antes, este código ficava junto com as fases anteriores da IC, no pacote `src.geracao`; aqui ele virou o pacote `geracao_netcdf`.

### Adicionado

- Núcleo de geração: grade diária em UTC no passo nominal, `qc_time`, `time_bounds` e escrita CF/ARM a partir do esquema de metadados do grupo.
- Validador com perfil do padrão ARM 1.3, pontos de controle e comparação com gabarito.
- Plug-in do RD-80 do ATTO-Campina, conferido com o RD80-Processing 3.1 e com o guia do DISDRODATA 2.0.
- Molde de esquema a partir de um NetCDF ou do DOD da ARM; esquema, constantes e física do Parsivel².
- Agente (modelo de linguagem num laço com o validador), clientes para modelos locais e por API, e benchmark com 7 formatos de arquivo do RD-80.
- Notebook do Google Colab que usa a pasta do Drive ou baixa o repositório, testes automáticos e integração contínua no GitHub Actions.
