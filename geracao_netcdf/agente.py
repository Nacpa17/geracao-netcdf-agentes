"""Agente que escreve o plug-in de um aparelho e só para quando o NetCDF
gerado passa no validador.

    tarefa (descrição + amostras + esquema + constantes)
      -> prompt -> modelo de linguagem -> código
      -> checagem estática (imports, escrita em disco, contrato)
      -> roda o gerador num subprocesso (com tempo limite)
      -> validador (esquema) -> pontos de controle -> [referência]
      -> erro? devolve o relatório ao modelo e tenta de novo

Os "pontos de controle" são alguns valores que a pessoa conhece (ex.: a taxa
de chuva que o próprio aparelho mostrou num minuto de chuva). Eles pegam o
que o esquema não pega: fuso horário errado, horário de fim em vez de
início, unidade trocada.

Uso:
    python -m geracao_netcdf.agente --tarefa tarefas/minha_tarefa.yaml \\
        --llm hf:Qwen/Qwen2.5-Coder-7B-Instruct
    python -m geracao_netcdf.agente --tarefa tarefas/minha_tarefa.yaml \\
        --llm anthropic:claude-sonnet-5-5           # precisa de ANTHROPIC_API_KEY

Atenção: o código escrito pelo modelo é EXECUTADO. A checagem estática
barra o óbvio (os, subprocess, gravar arquivos, eval...), e a execução é
num subprocesso com tempo limite e sem as variáveis de ambiente com chaves,
mas isso não é uma sandbox: rode em ambiente descartável (ex.: Colab).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np
import pandas as pd

from geracao_netcdf.aparelhos import CONTRATO, caminho_do_aparelho
from geracao_netcdf.esquema import Esquema, carregar_constantes, carregar_esquema
from geracao_netcdf.llm import ClienteLLM, criar_cliente
from geracao_netcdf.validador import validar

RAIZ = Path(__file__).resolve().parents[1]   # raiz do repositório

IMPORTS_PERMITIDOS = {
    "__future__", "numpy", "pandas", "math", "re", "datetime", "zoneinfo", "io",
    "csv", "json", "pathlib", "typing", "collections", "itertools", "functools",
    "statistics", "warnings", "dataclasses", "decimal", "string",
}
# plug-ins já validados podem ser reaproveitados (ex.: a física do rd80)
PREFIXO_PERMITIDO = "geracao_netcdf.aparelhos"
NOMES_PROIBIDOS = {"eval", "exec", "compile", "__import__", "globals", "locals",
                   "vars", "getattr", "setattr", "delattr", "breakpoint", "input",
                   "exit", "quit", "help", "memoryview"}
ATRIBUTOS_PROIBIDOS = {
    "system", "popen", "remove", "unlink", "rmdir", "rmtree", "removedirs",
    # ("rename" e "replace" ficam de fora: DataFrame.rename/str.replace são comuns)
    "write_text", "write_bytes", "touch", "mkdir", "makedirs", "chmod", "chown",
    "symlink_to", "hardlink_to", "to_csv", "to_netcdf", "to_pickle",
    "to_parquet", "to_excel", "to_hdf", "to_sql", "to_json", "to_feather",
    "to_stata", "to_orc", "savetxt", "save", "savez", "savez_compressed",
    "tofile", "dump",
}
CRIADAS_PELO_NUCLEO = ("base_time", "time_offset", "time", "qc_time")
SEGREDO = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.I)

SISTEMA = """Você é um engenheiro de dados científicos. Escreve plug-ins Python que \
convertem arquivos brutos de instrumentos meteorológicos em NetCDF no padrão ARM/CF.
Um núcleo pronto já cuida da grade de tempo diária, do qc_time e da escrita do NetCDF \
com os metadados do esquema; você escreve SÓ o plug-in.

Regras:
- Responda com UM bloco ```python contendo o módulo completo (nada fora do bloco).
- Implemente as três funções do contrato: ler, dimensoes, calcular.
- Imports permitidos: numpy, pandas, math, re, datetime, zoneinfo, io, csv, json, \
pathlib, typing, collections, itertools, functools, statistics, warnings, \
dataclasses, decimal, string e plug-ins já validados de geracao_netcdf.aparelhos.
- Não grave arquivos, não acesse a rede, não use os/sys/subprocess/eval/exec/getattr.
- Constantes do aparelho (área, diâmetros, velocidades, datas...) vêm de `const`; \
não as copie para o código.
- Valor ausente = NaN. Não invente nem interpole dados."""


# --------------------------------------------------------------------------
# tarefa
# --------------------------------------------------------------------------

@dataclass
class PontoControle:
    tempo: str                  # horário nominal em UTC, "aaaa-mm-dd hh:mm:ss"
    variavel: str
    valor: float
    tolerancia: float = 1e-3    # absoluta
    tolerancia_rel: float = 1e-3


@dataclass
class Tarefa:
    nome: str
    descricao: str
    amostras: list[Path]
    esquema: Path
    constantes: Path
    documentacao: Path | None = None
    exemplo: str | None = None          # aparelho registrado (ex.: "rd80") ou .py
    pontos_de_controle: list[PontoControle] = field(default_factory=list)
    referencia: Path | None = None      # NetCDF(s)-gabarito, se existirem
    max_iteracoes: int = 5
    timeout_s: int = 300
    linhas_amostra: int = 12

    @classmethod
    def de_yaml(cls, caminho: str | Path) -> "Tarefa":
        from geracao_netcdf.utils import load_yaml

        caminho = Path(caminho)
        d = load_yaml(caminho)
        base = caminho.parent

        def p(x):
            if x is None:
                return None
            x = Path(x).expanduser()
            return x if x.is_absolute() else (base / x).resolve()

        amostras: list[Path] = []
        for a in ([d["amostras"]] if isinstance(d["amostras"], str) else d["amostras"]):
            q = p(a)
            amostras += sorted(x for x in q.iterdir() if x.is_file()
                               and not x.name.startswith(".")) if q.is_dir() else [q]
        faltam = [str(a) for a in amostras if not a.exists()]
        if faltam:
            raise ValueError(f"{caminho}: amostras não encontradas: {', '.join(faltam)} "
                             "(crie a pasta e ponha 1-3 arquivos brutos de exemplo)")
        if not amostras:
            raise ValueError(f"{caminho}: nenhum arquivo bruto em `amostras` "
                             "(ponha 1-3 arquivos de exemplo)")
        exemplo = d.get("exemplo")
        if exemplo and str(exemplo).endswith(".py"):
            exemplo = str(p(exemplo))           # caminho relativo ao YAML
        aparelho = d.get("aparelho_base")       # usa esquema/constantes de um registrado
        pasta = caminho_do_aparelho(aparelho) if aparelho else None
        if pasta is None and not (d.get("esquema") and d.get("constantes")):
            raise ValueError(f"{caminho}: informe `esquema` e `constantes` "
                             "(ou `aparelho_base` para usar os de um aparelho registrado)")
        return cls(
            nome=d.get("nome", caminho.stem), descricao=d.get("descricao", ""),
            amostras=amostras,
            esquema=p(d.get("esquema")) or pasta / "netCDF_info.json",
            constantes=p(d.get("constantes")) or pasta / "variables_info.json",
            documentacao=p(d.get("documentacao")),
            exemplo=exemplo,
            pontos_de_controle=[PontoControle(**x) for x in d.get("pontos_de_controle") or []],
            referencia=p(d.get("referencia")),
            max_iteracoes=int(d.get("max_iteracoes", 5)),
            timeout_s=int(d.get("timeout_s", 300)))


# --------------------------------------------------------------------------
# prompt
# --------------------------------------------------------------------------

def _linha_curta(s: str, n: int = 400) -> str:
    return s if len(s) <= n else s[:n] + f" … (+{len(s) - n} caracteres)"


def amostra_de_arquivo(caminho: Path, n_linhas: int = 12, n_sinal: int = 4) -> str:
    """Primeiras linhas + algumas linhas "com sinal" (mais dígitos 1-9), para
    o modelo ver valores não nulos mesmo se o começo do arquivo for seco."""
    bruto = caminho.read_bytes()
    try:
        texto, cod = bruto.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        texto, cod = bruto.decode("latin-1"), "latin-1"
    fim = "CRLF (\\r\\n)" if "\r\n" in texto else "LF (\\n)"
    linhas = texto.splitlines()
    partes = [f"### {caminho.name}",
              f"{len(bruto)} bytes, {len(linhas)} linhas, codificação {cod}, fim de linha {fim}",
              "```text", *(_linha_curta(x) for x in linhas[:n_linhas]), "```",
              "Primeiras 3 linhas com repr() (mostra TAB, espaços e \\r):",
              "```text", *(_linha_curta(repr(x)) for x in linhas[:3]), "```"]
    resto = list(enumerate(linhas[n_linhas:], start=n_linhas + 1))
    if resto and n_sinal:
        score = sorted(resto, key=lambda t: -len(re.findall(r"[1-9]", t[1])))[:n_sinal]
        if score and re.search(r"[1-9]", score[0][1]):
            partes += ["Algumas linhas com valores não nulos (nº da linha: conteúdo):",
                       "```text", *(f"{i}: {_linha_curta(x)}" for i, x in sorted(score)), "```"]
    return "\n".join(partes)


def resumo_esquema(esquema: Esquema) -> str:
    dims = {d for v in esquema.variaveis.values() for d in v.dims}
    linhas = []
    for nome in esquema.obrigatorias:
        v = esquema.variaveis[nome]
        a = v.atributos
        if nome in CRIADAS_PELO_NUCLEO:
            origem = "o núcleo cria — NÃO devolva"
        elif v.valor is not None:
            origem = f"valor fixo do esquema ({v.valor}) — NÃO devolva"
        elif nome in dims:
            origem = "coordenada: vem de dimensoes() (pode devolver também)"
        else:
            origem = "calcular() devolve"
        dim = "(" + ", ".join(v.dims) + ")" if v.dims else "escalar"
        txt = f"- `{nome}` {dim} {v.tipo}, units={a.get('units')!r}: {a.get('long_name', '')} — {origem}"
        extras = [f"{k}: {a[k]}" for k in sorted(a) if re.fullmatch(r"bit_\d+_description", k)
                  or k in ("comment", "description", "flag_meanings")]
        if extras:
            txt += "\n    " + "\n    ".join(extras)
        linhas.append(txt)
    if esquema.proibidas:
        linhas.append("Não devolva (o esquema marca como opcional/não gravar): "
                      + ", ".join(esquema.proibidas))
    bits = sorted((k, v) for k, v in esquema.globais.items()
                  if re.fullmatch(r"qc_bit_\d+_(description|assessment)", k) and v)
    if bits:
        linhas.append("Bits de QC definidos nos atributos globais (valem para as qc_* "
                      "sem descrição própria):\n" + "\n".join(f"    {k}: {v}" for k, v in bits))
    return "\n".join(linhas)


def _codigo_exemplo(ref: str) -> tuple[str, str]:
    p = Path(ref)
    if p.suffix == ".py" and p.exists():
        return p.name, p.read_text(encoding="utf-8")
    pasta = caminho_do_aparelho(ref)
    return f"geracao_netcdf/aparelhos/{ref}/__init__.py", (pasta / "__init__.py").read_text(encoding="utf-8")


def montar_prompt(t: Tarefa) -> str:
    esquema = carregar_esquema(t.esquema)
    const = carregar_constantes(t.constantes)
    partes = [f"# Tarefa: plug-in para {t.nome}", t.descricao.strip(),
              "## Contrato do plug-in", f"```python\n{CONTRATO.strip()}\n```",
              "`ler` recebe a lista de caminhos dos arquivos brutos e `const` (abaixo). "
              "Se `const` tiver `Columns`/`drop_col`, devolva as colunas com esses nomes "
              "canônicos (as de data/hora viram o índice), mesmo que o arquivo use outros "
              "nomes, outra ordem ou outro separador.",
              "## Variáveis do esquema", resumo_esquema(esquema),
              f"## Constantes (`const`, de {t.constantes.name})",
              f"```json\n{json.dumps(const, ensure_ascii=False, indent=1)}\n```"]
    if t.documentacao:
        partes += ["## Documentação do aparelho", t.documentacao.read_text(encoding="utf-8")]
    if t.exemplo:
        nome, codigo = _codigo_exemplo(t.exemplo)
        modulo = f"geracao_netcdf.aparelhos.{t.exemplo}" if not t.exemplo.endswith(".py") else None
        partes += [f"## Exemplo: plug-in já validado ({nome})",
                   "Ele lê OUTRO formato de arquivo; use como modelo de estrutura e física."
                   + (f" Você pode importar dele o que servir (ex.: `from {modulo} import "
                      "calcular, dimensoes`)." if modulo else ""),
                   f"```python\n{codigo.strip()}\n```"]
    partes += ["## Amostras dos dados brutos",
               *(amostra_de_arquivo(a, t.linhas_amostra) for a in t.amostras[:2])]
    if len(t.amostras) > 2:
        partes.append(f"(mais {len(t.amostras) - 2} arquivo(s) no mesmo formato)")
    if t.pontos_de_controle:
        partes += ["## Pontos de controle (valores conhecidos que o resultado deve ter)",
                   *(f"- {c.variavel} em {c.tempo} UTC = {c.valor}" for c in t.pontos_de_controle)]
    partes.append("Escreva o módulo completo num único bloco ```python.")
    return "\n\n".join(partes)


# --------------------------------------------------------------------------
# código: extração e checagem estática
# --------------------------------------------------------------------------

def extrair_codigo(resposta: str) -> str | None:
    blocos = re.findall(r"```[ \t]*(?:python3|python|py)?[ \t]*\n(.*?)(?:```|\Z)",
                        resposta, flags=re.S | re.I)
    blocos = [b.strip("\n") for b in blocos if b.strip()]
    if blocos:
        com_contrato = [b for b in blocos if "def ler" in b and "def calcular" in b]
        return max(com_contrato or blocos, key=len) + "\n"
    if "def ler" in resposta:
        try:
            ast.parse(resposta)
            return resposta
        except SyntaxError:
            return None
    return None


def _modo_proibido(no: ast.Call, metodo: bool) -> str | None:
    """open(arq, modo) e Path.open(modo): só leitura, com modo literal."""
    keyword = [k.value for k in no.keywords if k.arg == "mode"]
    for c in keyword + ([] if metodo else list(no.args[1:2])):
        if not isinstance(c, ast.Constant) or not isinstance(c.value, str):
            return "o modo de open() precisa ser um texto literal ('r' ou 'rb')"
    candidatos = keyword + list(no.args[:2] if metodo else no.args[1:2])
    for c in candidatos:
        if (isinstance(c, ast.Constant) and isinstance(c.value, str)
                and re.fullmatch(r"[rwxabtU+]{1,4}", c.value) and set(c.value) & set("wax+")):
            return f"open() com modo {c.value!r}: o plug-in só pode LER arquivos"
    return None


def checar_codigo(codigo: str) -> list[str]:
    try:
        arvore = ast.parse(codigo)
    except SyntaxError as e:
        return [f"erro de sintaxe na linha {e.lineno}: {e.msg}"]
    problemas: list[str] = []
    definidas = {n.name for n in arvore.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for n in arvore.body:          # também aceita "from ... import calcular"
        if isinstance(n, ast.ImportFrom):
            definidas |= {a.asname or a.name for a in n.names}
        elif isinstance(n, ast.Assign):
            definidas |= {t.id for t in n.targets if isinstance(t, ast.Name)}
    for f in ("ler", "dimensoes", "calcular"):
        if f not in definidas:
            problemas.append(f"falta a função `{f}` do contrato")
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            for a in no.names:
                raiz = a.name.split(".")[0]
                if raiz not in IMPORTS_PERMITIDOS and not a.name.startswith(PREFIXO_PERMITIDO):
                    problemas.append(f"linha {no.lineno}: import não permitido: {a.name}")
        elif isinstance(no, ast.ImportFrom):
            mod = no.module or ""
            if no.level or (mod.split(".")[0] not in IMPORTS_PERMITIDOS
                            and not mod.startswith(PREFIXO_PERMITIDO)):
                problemas.append(f"linha {no.lineno}: import não permitido: from {'.' * no.level}{mod}")
        elif isinstance(no, ast.Name) and no.id in NOMES_PROIBIDOS:
            problemas.append(f"linha {no.lineno}: `{no.id}` não é permitido")
        elif isinstance(no, ast.Attribute):
            if no.attr.startswith("__") and no.attr.endswith("__"):
                problemas.append(f"linha {no.lineno}: atributo especial `{no.attr}` não é permitido")
            elif no.attr in ATRIBUTOS_PROIBIDOS:
                problemas.append(f"linha {no.lineno}: `.{no.attr}()` não é permitido "
                                 "(o plug-in não grava nem apaga arquivos)")
        elif isinstance(no, ast.Call):
            f = no.func
            nome = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if nome == "open":
                msg = _modo_proibido(no, metodo=isinstance(f, ast.Attribute))
                if msg:
                    problemas.append(f"linha {no.lineno}: {msg}")
    return list(dict.fromkeys(problemas))


# --------------------------------------------------------------------------
# execução e conferência
# --------------------------------------------------------------------------

@dataclass
class Execucao:
    codigo_saida: int
    stdout: str
    stderr: str
    duracao_s: float
    arquivos: list[str]
    estourou_tempo: bool = False


def ambiente_limpo() -> dict[str, str]:
    """Variáveis de ambiente sem chaves/tokens, para o subprocesso."""
    env = {k: v for k, v in os.environ.items() if not SEGREDO.search(k)}
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    return env


def executar_plugin(plugin: Path, t: Tarefa, saida: Path) -> Execucao:
    if saida.exists():
        shutil.rmtree(saida)
    saida.mkdir(parents=True)
    cmd = [sys.executable, "-m", "geracao_netcdf.gerar", "--modulo", str(plugin),
           "--esquema", str(t.esquema), "--constantes", str(t.constantes),
           "--entrada", *map(str, t.amostras), "--saida", str(saida)]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=RAIZ, capture_output=True, text=True,
                           timeout=t.timeout_s, env=ambiente_limpo())
        cod, out, err, estourou = r.returncode, r.stdout, r.stderr, False
    except subprocess.TimeoutExpired as e:
        cod, estourou = -9, True
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = f"tempo limite de {t.timeout_s} s estourado"
    return Execucao(cod, out, err, time.time() - t0,
                    sorted(str(p) for p in saida.glob("*.nc")), estourou)


def _tempos_do_arquivo(nc) -> pd.DatetimeIndex:
    unidades = nc["time"].units
    m = re.search(r"since\s+(\d{4}-\d{1,2}-\d{1,2})(?:[ T](\d{1,2}:\d{2}:\d{2}))?", unidades)
    ref = pd.Timestamp(f"{m.group(1)} {m.group(2) or '00:00:00'}")
    return ref + pd.to_timedelta(np.asarray(nc["time"][:], dtype=float), unit="s")


def conferir_pontos(arquivos: list[str], pontos: list[PontoControle]) -> list[str]:
    """Compara valores conhecidos com o NetCDF. Se não bate, procura o valor
    esperado em outro horário (±1 dia) para sugerir deslocamento de tempo."""
    series: dict[str, pd.Series] = {}

    def serie(var: str) -> pd.Series | None:
        if var not in series:
            partes = []
            for f in arquivos:
                with netCDF4.Dataset(f) as nc:
                    if var not in nc.variables or "time" not in nc.variables:
                        continue
                    v = nc[var]
                    v.set_auto_mask(False)
                    if v.dtype is str or np.dtype(v.dtype).kind not in "fiu":
                        continue
                    x = np.asarray(v[:], dtype=float)
                    if x.ndim != 1:
                        continue
                    mv = getattr(v, "missing_value", None)
                    if mv is not None:
                        x = np.where(np.isclose(x, float(mv)), np.nan, x)
                    partes.append(pd.Series(x, index=_tempos_do_arquivo(nc)))
            series[var] = pd.concat(partes).sort_index() if partes else None
        return series[var]

    falhas: list[str] = []
    deslocamentos: list[pd.Timedelta] = []
    for c in pontos:
        s = serie(c.variavel)
        quando = pd.Timestamp(c.tempo)
        if quando.tz is not None:
            quando = quando.tz_convert("UTC").tz_localize(None)
        if s is None:
            falhas.append(f"{c.variavel}: variável ausente (ou não é só de tempo) nos arquivos")
            continue
        tol = max(c.tolerancia, c.tolerancia_rel * abs(c.valor))
        obtido = s.get(quando, np.nan)
        if isinstance(obtido, pd.Series):
            obtido = obtido.iloc[0]
        if np.isfinite(obtido) and abs(obtido - c.valor) <= tol:
            continue
        msg = (f"{c.variavel} em {quando:%Y-%m-%d %H:%M:%S} UTC: esperado {c.valor}, "
               f"obtido {'ausente' if not np.isfinite(obtido) else f'{obtido:.6g}'}")
        janela = s[(s.index >= quando - pd.Timedelta(days=1)) & (s.index <= quando + pd.Timedelta(days=1))]
        perto = janela[np.isfinite(janela) & (np.abs(janela - c.valor) <= tol)]
        if len(perto):
            desloc = sorted(perto.index - quando, key=abs)[0]
            deslocamentos.append(desloc)
            msg += f" — o valor esperado aparece {_fmt_desloc(desloc)} deste horário"
        falhas.append(msg)
    if deslocamentos:
        comum, vezes = pd.Series(deslocamentos).value_counts().index[0], \
            int(pd.Series(deslocamentos).value_counts().iloc[0])
        falhas.append(f"Resumo: em {vezes} de {len(pontos)} pontos o valor esperado está "
                      f"{_fmt_desloc(comum)} — horário deslocado (fuso? início x fim do "
                      "intervalo?).")
    return falhas


def _fmt_desloc(td: pd.Timedelta) -> str:
    s = int(abs(td.total_seconds()))
    h, resto = divmod(s, 3600)
    m, s = divmod(resto, 60)
    partes = [f"{h} h" if h else "", f"{m} min" if m else "", f"{s} s" if s else ""]
    texto = " ".join(p for p in partes if p) or "0 s"
    return f"{texto} {'depois' if td > pd.Timedelta(0) else 'antes'}"


def resumo_variaveis(arquivo: str, max_vars: int = 30) -> str:
    linhas = []
    with netCDF4.Dataset(arquivo) as nc:
        nc.set_auto_mask(False)
        for nome, v in list(nc.variables.items())[:max_vars]:
            if v.dtype is str or np.dtype(v.dtype).kind not in "fiu" \
                    or "time" not in v.dimensions:
                continue
            x = np.asarray(v[:], dtype=float)
            mv = getattr(v, "missing_value", None)
            ausente = ~np.isfinite(x)
            if mv is not None:
                ausente |= np.isclose(x, float(mv))
            ok = x[~ausente]
            pct = 100.0 * ausente.mean() if x.size else 100.0
            faixa = f"{ok.min():.4g} .. {ok.max():.4g}" if ok.size else "—"
            linhas.append(f"{nome}: {faixa} ({pct:.0f}% ausente)")
    return "\n".join(linhas)


# --------------------------------------------------------------------------
# o laço
# --------------------------------------------------------------------------

@dataclass
class Tentativa:
    numero: int
    etapa: str              # extracao | estatico | execucao | validacao | controle | referencia | ok
    problemas: list[str]
    plugin: str | None = None
    arquivos: list[str] = field(default_factory=list)
    duracao_llm_s: float = 0.0
    duracao_exec_s: float = 0.0
    validacao: list[dict] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


@dataclass
class ResultadoAgente:
    tarefa: str
    llm: str
    sucesso: bool
    pasta: str
    plugin_final: str | None
    ultimo_plugin_executado: str | None
    tentativas: list[Tentativa]
    arquivos_finais: list[str] = field(default_factory=list)  # NetCDFs do plug-in final
                                                               # (ou do último que rodou)

    @property
    def iteracoes(self) -> int:
        return len(self.tentativas)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["iteracoes"] = self.iteracoes
        return d


def _feedback(t: Tentativa, max_chars: int = 6000) -> str:
    cab = {"extracao": "não encontrei o código na resposta",
           "estatico": "o código não passou na checagem estática",
           "execucao": "o gerador falhou ao rodar o plug-in",
           "validacao": "os NetCDFs gerados não passaram no validador",
           "controle": "os NetCDFs não batem com os pontos de controle",
           "referencia": "os NetCDFs diferem da referência"}[t.etapa]
    partes = [f"A tentativa {t.numero} falhou: {cab}."]
    if t.etapa == "extracao":
        partes.append("Responda com UM bloco ```python com as funções ler, dimensoes e calcular.")
    elif t.etapa == "execucao":
        erro = "\n".join(t.stderr.strip().splitlines()[-40:])
        partes += ["Saída de erro (fim):", "```text", erro[-max_chars:], "```"]
        if t.stdout.strip():
            partes += ["Saída normal (fim):", "```text",
                       "\n".join(t.stdout.strip().splitlines()[-10:]), "```"]
    else:
        partes += [f"- {p}" for p in t.problemas[:40]]
        if t.arquivos and t.etapa in ("validacao", "controle", "referencia"):
            partes += [f"Resumo das variáveis de {Path(t.arquivos[0]).name} (mín .. máx):",
                       "```text", resumo_variaveis(t.arquivos[0]), "```"]
    partes.append("Corrija e responda com o módulo COMPLETO num único bloco ```python.")
    return "\n".join(partes)[: max_chars * 2]


def rodar_agente(t: Tarefa, cliente: ClienteLLM, pasta: str | Path,
                 historico: str = "ultima", log=print) -> ResultadoAgente:
    """Roda o laço até o plug-in passar ou acabarem as iterações.

    historico="ultima" manda ao modelo só o pedido + a última tentativa e o
    retorno dela (contexto curto, bom para modelos pequenos); "completo"
    manda a conversa inteira."""
    pasta = Path(pasta).resolve()
    pasta.mkdir(parents=True, exist_ok=True)
    esquema = carregar_esquema(t.esquema)
    pedido = montar_prompt(t)
    (pasta / "prompt.md").write_text(pedido, encoding="utf-8")
    conversa: list[dict] = [{"role": "system", "content": SISTEMA},
                            {"role": "user", "content": pedido}]
    tentativas: list[Tentativa] = []
    final = ultimo = None
    for n in range(1, t.max_iteracoes + 1):
        t0 = time.time()
        try:
            resposta = cliente.conversar(conversa)
        except Exception as e:  # noqa: BLE001 - API fora do ar, roteiro acabou...
            tentativas.append(Tentativa(numero=n, etapa="modelo",
                                        problemas=[f"{type(e).__name__}: {e}"],
                                        duracao_llm_s=round(time.time() - t0, 2)))
            log(f"[{t.nome}] tentativa {n}: o modelo falhou ({type(e).__name__}: {e})")
            break
        dur_llm = time.time() - t0
        (pasta / f"resposta_{n}.md").write_text(resposta, encoding="utf-8")
        codigo = extrair_codigo(resposta)
        tent = Tentativa(numero=n, etapa="ok", problemas=[], duracao_llm_s=round(dur_llm, 2))
        if codigo is None:
            tent.etapa = "extracao"
        else:
            plugin = pasta / f"plugin_{n}.py"
            plugin.write_text(codigo, encoding="utf-8")
            tent.plugin = str(plugin)
            tent.problemas = checar_codigo(codigo)
            if tent.problemas:
                tent.etapa = "estatico"
            else:
                ex = executar_plugin(plugin, t, pasta / f"saida_{n}")
                tent.duracao_exec_s = round(ex.duracao_s, 2)
                tent.arquivos = ex.arquivos
                tent.stdout, tent.stderr = ex.stdout[-8000:], ex.stderr[-8000:]
                if ex.codigo_saida != 0 or not ex.arquivos:
                    tent.etapa = "execucao"
                    if not ex.stderr.strip():
                        tent.stderr = "nenhum NetCDF gerado (código de saída " \
                                      f"{ex.codigo_saida})\n" + ex.stdout
                else:
                    ultimo = plugin
                    resultados = [validar(f, esquema) for f in ex.arquivos]
                    tent.validacao = [{"arquivo": Path(r.arquivo).name, "nota": r.nota,
                                       "erros": len(r.erros), "avisos": len(r.avisos)}
                                      for r in resultados]
                    erros = [f"{Path(r.arquivo).name}: {p.codigo}"
                             + (f" [{p.variavel}]" if p.variavel else "") + f": {p.mensagem}"
                             for r in resultados for p in r.erros]
                    if erros:
                        tent.etapa, tent.problemas = "validacao", erros
                    elif t.pontos_de_controle and (falhas := conferir_pontos(ex.arquivos, t.pontos_de_controle)):
                        tent.etapa, tent.problemas = "controle", falhas
                    elif t.referencia:
                        dif = []
                        if Path(t.referencia).is_dir():
                            gerados = {Path(f).name for f in ex.arquivos}
                            dif += [f"faltou gerar {g.name} (existe na referência)"
                                    for g in sorted(Path(t.referencia).glob("*.nc"))
                                    if g.name not in gerados]
                        for f in ex.arquivos:
                            r = validar(f, esquema, referencia=t.referencia)
                            dif += [f"{Path(f).name}: {p.codigo} [{p.variavel}]: {p.mensagem}"
                                    for p in r.erros if p.codigo.startswith("R_")]
                        if dif:
                            tent.etapa, tent.problemas = "referencia", dif
        tentativas.append(tent)
        log(f"[{t.nome}] tentativa {n}: {tent.etapa}"
            + (f" ({len(tent.problemas)} problema(s))" if tent.problemas else "")
            + f" | modelo {tent.duracao_llm_s:.1f} s, execução {tent.duracao_exec_s:.1f} s")
        if tent.etapa == "ok":
            final = pasta / "plugin_final.py"
            shutil.copyfile(tent.plugin, final)
            break
        retorno = _feedback(tent)
        (pasta / f"retorno_{n}.md").write_text(retorno, encoding="utf-8")
        if historico == "completo":
            conversa += [{"role": "assistant", "content": resposta},
                         {"role": "user", "content": retorno}]
        else:
            conversa = conversa[:2] + [{"role": "assistant", "content": resposta},
                                       {"role": "user", "content": retorno}]
    usada = next((x for x in reversed(tentativas)
                  if x.etapa == "ok" or (ultimo and x.plugin == str(ultimo))), None)
    res = ResultadoAgente(tarefa=t.nome, llm=getattr(cliente, "nome", "llm"),
                          sucesso=final is not None, pasta=str(pasta),
                          plugin_final=str(final) if final else None,
                          ultimo_plugin_executado=str(ultimo) if ultimo else None,
                          tentativas=tentativas,
                          arquivos_finais=list(usada.arquivos) if usada else [])
    (pasta / "relatorio.json").write_text(json.dumps(res.to_dict(), ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tarefa", required=True, help="arquivo YAML da tarefa")
    ap.add_argument("--llm", required=True,
                    help="roteiro:<arq>, hf:<modelo>, openai:<modelo>[@url], anthropic:<modelo>")
    ap.add_argument("--saida", help="pasta do relatório (padrão: runs/agente/<tarefa>)")
    ap.add_argument("--max-iteracoes", type=int)
    ap.add_argument("--historico", choices=["ultima", "completo"], default="ultima")
    ap.add_argument("--temperatura", type=float, default=None)
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--4bit", dest="quatro_bits", action="store_true",
                    help="hf: carrega o modelo em 4 bits (GPU pequena, ex. T4)")
    a = ap.parse_args()
    t = Tarefa.de_yaml(a.tarefa)
    if a.max_iteracoes:
        t.max_iteracoes = a.max_iteracoes
    cliente = criar_cliente(a.llm, temperatura=a.temperatura, max_tokens=a.max_tokens,
                            quantizar_4bit=a.quatro_bits)
    pasta = Path(a.saida or RAIZ / "runs" / "agente" / t.nome)
    res = rodar_agente(t, cliente, pasta, historico=a.historico)
    if res.sucesso:
        print(f"OK em {res.iteracoes} tentativa(s): {res.plugin_final}")
        print("Para registrar o aparelho: copie o plug-in para "
              "geracao_netcdf/aparelhos/<nome>/__init__.py junto com o esquema e as constantes.")
    else:
        print(f"Sem sucesso em {res.iteracoes} tentativa(s). Veja {pasta}/relatorio.json "
              "e os arquivos retorno_*.md.")
    sys.exit(0 if res.sucesso else 1)


if __name__ == "__main__":
    main()
