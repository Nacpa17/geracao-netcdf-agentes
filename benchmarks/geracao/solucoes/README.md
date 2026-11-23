# Respostas gravadas para o benchmark de geração

Uma solução correta por formato de arquivo, escrita à mão. Servem para
conferir o circuito do benchmark sem modelo de linguagem:

    python -m geracao_netcdf.bench --llm roteiro:benchmarks/geracao/solucoes

tem de dar 7/7. `hora_local/` tem DUAS respostas: a primeira esquece o fuso
(de propósito) e os pontos de controle reprovam; a segunda corrige — mostra
o laço de correção do agente funcionando.

Todas reaproveitam a física validada do plug-in `rd80` e só trocam o `ler`.
