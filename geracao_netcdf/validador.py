"""Validador automático de NetCDF: diz se um arquivo gerado está certo.

É o juiz do agente: cada script novo só é aceito se o arquivo que ele gera
passa aqui sem erros. Confere:

1. estrutura x esquema: variáveis obrigatórias, proibidas (Optional=true) e
   extras; dimensões; tipos;
2. metadados de variável: long_name e units; missing_value/valid_min/
   valid_max com o tipo da variável; ancillary_variables e bounds que citam
   variáveis existentes; campos internos de catálogo vazando;
3. QC: todo bit usado num qc_* está descrito (flag_masks, bit_N_description
   na variável ou qc_bit_N_description global, como na ARM);
4. valores: NaN onde deveria haver missing_value; fora de valid_min/max;
   tempo crescente e coerente com base_time + time_offset; arquivo vazio;
5. atributos globais: os obrigatórios do esquema, sem "__AUTO__"/"TBD";
   Conventions; history; unidade em sampling_interval etc.; nome do arquivo;
6. unidades escritas de jeitos diferentes no mesmo arquivo;
7. (opcional) comparação com um NetCDF de referência (gabarito).

Nota = 100 - 15 x erros - 3 x avisos (mínimo 0). Aprovado = nenhum erro.

Uso:
    python -m geracao_netcdf.validador saida/*.nc --aparelho rd80
    python -m geracao_netcdf.validador saida/*.nc --aparelho rd80 --padrao arm
    python -m geracao_netcdf.validador arq.nc --esquema netCDF_info.json \\
        --referencia pasta_gabarito --json relatorio.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np
import pandas as pd

from geracao_netcdf.esquema import CAMPOS_CATALOGO, Esquema, carregar_esquema

TIPADOS = ("missing_value", "_FillValue", "valid_min", "valid_max",
           "valid_range", "flag_masks", "flag_values")
# grafias de "adimensional" que significam a mesma coisa. "none" e "count"
# ficam de fora: a própria ARM usa "none"/"count" para contagens e
# "unitless"/"1" para flags, de propósito
ADIMENSIONAIS = {"unitless", "1", "dimensionless"}
GLOBAIS_COM_UNIDADE = ("sampling_interval", "averaging_interval",
                       "effective_measurement_area")
PLACEHOLDERS = {"__AUTO__", "TBD"}


@dataclass
class Problema:
    codigo: str
    severidade: str           # "erro" | "aviso"
    mensagem: str
    variavel: str | None = None


@dataclass
class Resultado:
    arquivo: str
    problemas: list[Problema] = field(default_factory=list)
    comparacao: dict[str, Any] | None = None

    @property
    def erros(self) -> list[Problema]:
        return [p for p in self.problemas if p.severidade == "erro"]

    @property
    def avisos(self) -> list[Problema]:
        return [p for p in self.problemas if p.severidade == "aviso"]

    @property
    def aprovado(self) -> bool:
        return not self.erros

    @property
    def nota(self) -> int:
        return max(0, 100 - 15 * len(self.erros) - 3 * len(self.avisos))

    def to_dict(self) -> dict[str, Any]:
        return {"arquivo": self.arquivo, "aprovado": self.aprovado,
                "nota": self.nota, "n_erros": len(self.erros),
                "n_avisos": len(self.avisos),
                "problemas": [asdict(p) for p in self.problemas],
                "comparacao": self.comparacao}

    def texto(self, curto: bool = False, max_itens: int = 40) -> str:
        status = "APROVADO" if self.aprovado else "REPROVADO"
        linhas = [f"{Path(self.arquivo).name}: {status} | nota {self.nota} | "
                  f"{len(self.erros)} erro(s), {len(self.avisos)} aviso(s)"]
        itens = self.erros + ([] if curto else self.avisos)
        for p in itens[:max_itens]:
            alvo = f" [{p.variavel}]" if p.variavel else ""
            linhas.append(f"  {p.severidade.upper():5s} {p.codigo}{alvo}: {p.mensagem}")
        if len(itens) > max_itens:
            linhas.append(f"  ... e mais {len(itens) - max_itens}")
        if self.comparacao and not curto:
            c = self.comparacao
            linhas.append(f"  referência: {c['identicas']}/{c['comparadas']} variáveis "
                          f"iguais (tolerância rel {c['tolerancia_rel']})")
        return "\n".join(linhas)


def _bits_descritos(var, globais: dict[str, Any]) -> int | None:
    attrs = {a: var.getncattr(a) for a in var.ncattrs()}
    mascara = 0
    if "flag_masks" in attrs:
        for m in np.atleast_1d(attrs["flag_masks"]):
            try:
                mascara |= int(m)
            except (TypeError, ValueError):
                pass
    for fonte in (attrs, globais):
        for k in fonte:
            m = re.fullmatch(r"(?:qc_)?bit_(\d+)_description", k)
            if m and (fonte is attrs or k.startswith("qc_")):
                mascara |= 1 << (int(m.group(1)) - 1)
        if mascara:
            break
    return mascara or None


def _normalizar_unidade(u: str) -> str:
    """Chave para achar a MESMA unidade escrita de jeitos diferentes
    ("mm h-1" ~ "mm/h", "1/(m^3 mm)" ~ "1/(m^3-mm)"). Conservadora: na
    dúvida, chaves diferentes (só deixa de avisar)."""
    s = u.strip().lower()
    s = re.sub(r"\bhr\b", "h", s)
    s = re.sub(r"\s*\b([a-z]+)\s*-1\b", r"/\1", s)      # "mm h-1" -> "mm/h"
    s = s.replace("**", "").replace("^", "")
    s = re.sub(r"-(?!\d)", " ", s)                      # hífen separador; "m-3" fica
    return re.sub(r"[\s*.]+", "", s)


def _e_texto(var) -> bool:
    """Texto: vlen str (NetCDF4) ou matriz de char (NetCDF3, dtype S1)."""
    return var.dtype is str or np.dtype(var.dtype).kind in "SU"


def _valores(var) -> np.ndarray:
    try:
        return np.asarray(var[:])
    except Exception:          # noqa: BLE001 - strings/tipos exóticos
        return np.asarray([])


def _ausente(arr: np.ndarray, var) -> np.ndarray:
    """Máscara de valores ausentes (NaN ou igual ao missing_value)."""
    if arr.dtype.kind not in "fiu":
        return np.zeros(arr.shape, dtype=bool)
    m = np.isnan(arr) if arr.dtype.kind == "f" else np.zeros(arr.shape, dtype=bool)
    for a in ("missing_value", "_FillValue"):
        if a in var.ncattrs():
            try:
                mv = np.asarray(var.getncattr(a), dtype=float)
                m |= np.isin(arr.astype(float), np.atleast_1d(mv))
            except (TypeError, ValueError):
                pass
    return m


def _checar_variaveis(nc, esquema: Esquema | None, out: list[Problema]) -> None:
    globais = {a: nc.getncattr(a) for a in nc.ncattrs()}
    nomes = set(nc.variables)
    # variáveis de limites (CF 7.1) herdam units/long_name da coordenada
    limites = {b for v in nc.variables.values() if "bounds" in v.ncattrs()
               for b in str(v.getncattr("bounds")).split()}
    if esquema:
        for n in esquema.obrigatorias:
            if n not in nc.variables:
                out.append(Problema("E_VAR_FALTANDO", "erro",
                                    "variável obrigatória do esquema não está no arquivo", n))
        for n in esquema.proibidas:
            if n in nc.variables:
                out.append(Problema("E_VAR_PROIBIDA", "erro",
                                    "o esquema marca Optional=true (não gravar)", n))
        for n in nc.variables:
            if n not in esquema.variaveis:
                out.append(Problema("A_VAR_EXTRA", "aviso", "variável fora do esquema", n))
    for nome, var in nc.variables.items():
        attrs = {a: var.getncattr(a) for a in var.ncattrs()}
        spec = esquema.variaveis.get(nome) if esquema else None
        # texto: vlen str (NetCDF4) ou matriz de char (NetCDF3, dtype S1)
        texto = _e_texto(var)
        char = texto and var.dtype is not str
        if spec:
            dims_var = tuple(var.dimensions)
            if char and dims_var[:-1] == tuple(spec.dims):
                dims_var = dims_var[:-1]          # texto em NetCDF3: última dim = tamanho
            if dims_var != tuple(spec.dims):
                out.append(Problema("E_DIMS", "erro", f"dimensões {var.dimensions}, "
                                    f"o esquema pede {spec.dims}", nome))
            if spec.tipo == "str":
                if var.dtype is not str and not char:
                    out.append(Problema("E_TIPO", "erro", f"tipo {var.dtype}, esquema str", nome))
            elif var.dtype is str or np.dtype(var.dtype) != np.dtype(spec.tipo):
                out.append(Problema("E_TIPO", "erro", f"tipo {var.dtype}, esquema {spec.tipo}", nome))
        for a in ("long_name", "units"):
            if a not in attrs and not texto and nome not in limites:
                out.append(Problema("E_ATTR_FALTANDO", "erro", f"sem o atributo {a}", nome))
        internos = sorted(set(attrs) & CAMPOS_CATALOGO)
        if internos:
            out.append(Problema("A_ATTR_INTERNO", "aviso",
                                "campos internos do catálogo gravados como atributo: "
                                + ", ".join(internos), nome))
        for a in TIPADOS:
            if a not in attrs:
                continue
            val = attrs[a]
            if isinstance(val, str):
                out.append(Problema("E_ATTR_TIPO", "erro",
                                    f"{a} é texto ({val!r}) numa variável {var.dtype}", nome))
            elif not texto and np.asarray(val).dtype != np.dtype(var.dtype):
                out.append(Problema("E_ATTR_TIPO", "erro",
                                    f"{a} tem tipo {np.asarray(val).dtype}, a variável é "
                                    f"{var.dtype}", nome))
        for a in ("ancillary_variables", "bounds"):
            if a in attrs:
                citadas = [x for x in str(attrs[a]).split() if x not in nomes]
                if citadas:
                    out.append(Problema("E_ANCILLARY", "erro",
                                        f"{a} cita variáveis inexistentes: {', '.join(citadas)}",
                                        nome))
        if texto:
            continue
        arr = _valores(var)
        if nome.startswith("qc_"):
            mascara = _bits_descritos(var, globais)
            if mascara is None:
                out.append(Problema("A_QC_SEM_DESCRICAO", "aviso",
                                    "variável de QC sem descrição dos bits", nome))
            elif arr.size and arr.dtype.kind in "iu":
                validos = arr[~_ausente(arr, var)].astype(np.int64)
                fora = validos[(validos & ~mascara) != 0]
                if fora.size:
                    out.append(Problema("E_QC_BITS", "erro",
                                        f"{fora.size} valor(es) usam bits não descritos "
                                        f"(ex.: {int(fora[0])}; descritos: máscara {mascara})", nome))
        if arr.dtype.kind == "f" and arr.size:
            mv = attrs.get("missing_value")
            if mv is not None and not isinstance(mv, str) and np.isfinite(np.asarray(mv, float)).all():
                nan = int(np.isnan(arr).sum())
                if nan:
                    out.append(Problema("E_NAN", "erro", f"{nan} valor(es) NaN; o ausente "
                                        f"declarado é missing_value={mv}", nome))
        if arr.dtype.kind in "fiu" and arr.size:
            ok = arr[~_ausente(arr, var)].astype(float)
            for a, cmp in (("valid_min", np.less), ("valid_max", np.greater)):
                lim = attrs.get(a)
                if lim is None or isinstance(lim, str):
                    continue
                fora = ok[cmp(ok, float(np.asarray(lim)))]
                if fora.size:
                    out.append(Problema("E_FAIXA", "erro", f"{fora.size} valor(es) fora de "
                                        f"{a}={lim} (ex.: {fora[0]:.6g})", nome))


def _checar_tempo(nc, out: list[Problema]) -> None:
    if "time" not in nc.variables:
        out.append(Problema("E_TEMPO", "erro", "o arquivo não tem a variável time"))
        return
    t = np.asarray(nc["time"][:], dtype=float)
    if _ausente(t, nc["time"]).any():
        out.append(Problema("E_TEMPO", "erro", "time com valor ausente ou NaN "
                            "(ARM 1.3, 6.1.1; CF 5)", "time"))
    if t.size > 1 and not np.all(np.diff(t) > 0):
        out.append(Problema("E_TEMPO", "erro", "time não é estritamente crescente", "time"))
    _checar_limites_tempo(nc, t, out)
    unidades = getattr(nc["time"], "units", "")
    m = re.search(r"since\s+(\d{4}-\d{1,2}-\d{1,2})(?:[ T](\d{1,2}:\d{2}:\d{2}))?", unidades)
    if not m:
        out.append(Problema("E_TEMPO", "erro", f"units de time sem data de referência: "
                            f"{unidades!r}", "time"))
        return
    ref = pd.Timestamp(f"{m.group(1)} {m.group(2) or '00:00:00'}")
    if "base_time" in nc.variables and "time_offset" in nc.variables and t.size:
        base = float(np.asarray(nc["base_time"][:]))
        off = np.asarray(nc["time_offset"][:], dtype=float)
        epoca = ref.timestamp() + t
        if off.shape == t.shape and np.nanmax(np.abs(base + off - epoca)) > 1:
            out.append(Problema("E_TEMPO", "erro", "base_time + time_offset não coincide "
                                "com time (mais de 1 s)", "time_offset"))


def _checar_limites_tempo(nc, t: np.ndarray, out: list[Problema]) -> None:
    """time_bounds (CF 7.1; ARM 1.3, 6.1.4): sem ausentes, na mesma referência
    de time (sem units próprias) e contendo cada time."""
    nome = getattr(nc["time"], "bounds", None)
    if nome is None and "time_bounds" in nc.variables:
        nome = "time_bounds"
        out.append(Problema("A_TEMPO_LIMITES", "aviso", "time_bounds existe mas time não "
                            "tem o atributo bounds (CF 7.1)", "time"))
    if nome is None:
        return
    if nome not in nc.variables:
        out.append(Problema("E_TEMPO_LIMITES", "erro", f"time:bounds cita {nome!r}, que não "
                            "existe", "time"))
        return
    var = nc[nome]
    tb = np.asarray(var[:], dtype=float)
    if tb.ndim != 2 or tb.shape[1] != 2 or tb.shape[0] != t.shape[0]:
        out.append(Problema("E_TEMPO_LIMITES", "erro", f"forma {tb.shape}; esperado "
                            f"({t.shape[0]}, 2)", nome))
        return
    ausente = _ausente(tb, var).any(axis=1)
    if ausente.any():
        out.append(Problema("E_TEMPO_LIMITES", "erro", f"{int(ausente.sum())} linha(s) com "
                            "valor ausente: limites de tempo não podem faltar "
                            "(ARM 1.3, 6.1.4)", nome))
    if "units" in var.ncattrs() and var.units != getattr(nc["time"], "units", None):
        out.append(Problema("A_TEMPO_LIMITES", "aviso", f"units {var.units!r} diferente da de "
                            "time: limites devem usar a referência de time (CF 7.1)", nome))
    ok = ~ausente
    if not ok.any():
        return
    lo, hi, tt = tb[ok, 0], tb[ok, 1], t[ok]
    if np.any(hi < lo):
        out.append(Problema("E_TEMPO_LIMITES", "erro", "limite final antes do inicial", nome))
    fora = (tt < lo - 1) | (tt > hi + 1)
    if fora.any():
        longe = np.max(np.abs(np.where(tt < lo, lo - tt, tt - hi))[fora])
        grave = longe > 86400 or fora.mean() > 0.05
        out.append(Problema("E_TEMPO_LIMITES" if grave else "A_TEMPO_LIMITES",
                            "erro" if grave else "aviso",
                            f"{int(fora.sum())} intervalo(s) não contêm o time (até "
                            f"{longe:.6g} s de distância)"
                            + ("; os valores parecem estar em outra referência (ex.: "
                               "segundos desde 1970 sem units)" if longe > 86400 else ""), nome))


def _checar_vazio(nc, out: list[Problema]) -> None:
    limites = {b for v in nc.variables.values() if "bounds" in v.ncattrs()
               for b in str(v.getncattr("bounds")).split()} | {"time_bounds"}
    dados = [v for n, v in nc.variables.items()
             if "time" in v.dimensions and not n.startswith("qc_")
             and n not in ("time", "time_offset") and n not in limites
             and not _e_texto(v)]
    if not dados:
        return
    if all(_ausente(np.asarray(v[:]), v).all() for v in dados):
        out.append(Problema("E_VAZIO", "erro",
                            "nenhuma observação: todas as variáveis de dados estão ausentes"))


def _checar_globais(nc, esquema: Esquema | None, arquivo: Path,
                    out: list[Problema]) -> None:
    globais = {a: nc.getncattr(a) for a in nc.ncattrs()}
    minusculas = {k.lower(): k for k in globais}
    if esquema:
        for nome in esquema.globais:
            if nome in esquema.globais_opcionais:
                continue
            achado = globais.get(nome, globais.get(minusculas.get(nome.lower(), ""), None))
            if achado is None:
                out.append(Problema("E_GLOBAL_FALTANDO", "erro",
                                    f"atributo global obrigatório ausente: {nome}"))
    for k, v in globais.items():
        if isinstance(v, str) and v.strip() in PLACEHOLDERS:
            out.append(Problema("E_GLOBAL_PLACEHOLDER", "erro",
                                f"atributo global {k} ficou com {v!r}"))
    if "Conventions" not in globais:
        msg = ("'conventions' em minúsculas: o CF exige 'Conventions'"
               if "conventions" in globais else "sem o atributo global Conventions")
        out.append(Problema("A_CONVENTIONS", "aviso", msg))
    if "history" not in globais:
        out.append(Problema("A_HISTORY", "aviso", "sem o atributo global history"))
    for k in GLOBAIS_COM_UNIDADE:
        v = globais.get(k)
        if isinstance(v, str) and re.fullmatch(r"\s*-?\d+(\.\d+)?\s*", v):
            out.append(Problema("A_GLOBAL_SEM_UNIDADE", "aviso",
                                f"{k} = {v!r} sem unidade (ex.: '60 seconds')"))
    ds = globais.get("datastream")
    if isinstance(ds, str) and ds and not arquivo.name.startswith(ds + "."):
        out.append(Problema("A_NOME_ARQUIVO", "aviso",
                            f"nome do arquivo não começa pelo datastream {ds!r}"))


def _checar_unidades(nc, out: list[Problema]) -> None:
    grupos: dict[str, set[str]] = {}
    for var in nc.variables.values():
        u = getattr(var, "units", None)
        if not isinstance(u, str) or "since" in u:
            continue
        chave = "adimensional" if u.strip().lower() in ADIMENSIONAIS else _normalizar_unidade(u)
        grupos.setdefault(chave, set()).add(u)
    for chave, grafias in grupos.items():
        if len(grafias) > 1:
            out.append(Problema("A_UNIDADES_MISTAS", "aviso",
                                "a mesma unidade escrita de jeitos diferentes: "
                                + ", ".join(sorted(grafias))))


def _arquivo_referencia(referencia: Path, arquivo: Path) -> Path | None:
    if referencia.is_dir():
        alvo = referencia / arquivo.name
        return alvo if alvo.exists() else None
    return referencia if referencia.exists() else None


def comparar(nc, ref_path: Path, tol_rel: float, tol_abs: float,
             out: list[Problema]) -> dict[str, Any]:
    resumo: dict[str, Any] = {"referencia": str(ref_path), "comparadas": 0,
                              "identicas": 0, "diferentes": {},
                              "so_na_referencia": [], "tolerancia_rel": tol_rel}
    with netCDF4.Dataset(ref_path) as ref:
        ref.set_auto_mask(False)
        for nome, rvar in ref.variables.items():
            if nome not in nc.variables:
                resumo["so_na_referencia"].append(nome)
                continue
            var = nc.variables[nome]
            if _e_texto(var) or _e_texto(rvar):
                continue
            a, b = np.asarray(var[:]), np.asarray(rvar[:])
            resumo["comparadas"] += 1
            if a.shape != b.shape:
                resumo["diferentes"][nome] = f"forma {a.shape} x {b.shape}"
                out.append(Problema("R_FORMA", "erro", f"forma {a.shape}; referência "
                                    f"{b.shape}", nome))
                continue
            af = np.where(_ausente(a, var), np.nan, a.astype(float))
            bf = np.where(_ausente(b, rvar), np.nan, b.astype(float))
            difere = ~np.isclose(af, bf, rtol=tol_rel, atol=tol_abs, equal_nan=True)
            if difere.any():
                dif = np.abs(af - bf)
                maxdif = float(np.nanmax(dif)) if np.isfinite(dif).any() else float("nan")
                resumo["diferentes"][nome] = int(difere.sum())
                out.append(Problema("R_DIFERENCA", "erro", f"{int(difere.sum())} valor(es) "
                                    f"diferentes da referência (máx |dif| {maxdif:.4g}; "
                                    f"ausente num lado só também conta)", nome))
            else:
                resumo["identicas"] += 1
    if resumo["so_na_referencia"]:
        out.append(Problema("R_FALTANDO", "aviso", "variáveis da referência que não estão "
                            "no arquivo: " + ", ".join(resumo["so_na_referencia"][:15])
                            + (" ..." if len(resumo["so_na_referencia"]) > 15 else "")))
    return resumo


def validar(arquivo: str | Path, esquema: Esquema | str | Path | None = None,
            referencia: str | Path | None = None, tol_rel: float = 1e-5,
            tol_abs: float = 1e-6, padrao: str | None = None) -> Resultado:
    """`padrao="arm"` acrescenta o checklist do ARM Data File Standards 1.3
    (geracao_netcdf/padrao_arm.py)."""
    arquivo = Path(arquivo)
    if esquema is not None and not isinstance(esquema, Esquema):
        esquema = carregar_esquema(esquema)
    res = Resultado(arquivo=str(arquivo))
    try:
        nc = netCDF4.Dataset(arquivo)
    except (OSError, RuntimeError) as e:
        res.problemas.append(Problema("E_ARQUIVO", "erro", f"não abre como NetCDF: {e}"))
        return res
    with nc:
        nc.set_auto_mask(False)
        _checar_variaveis(nc, esquema, res.problemas)
        _checar_tempo(nc, res.problemas)
        _checar_vazio(nc, res.problemas)
        _checar_globais(nc, esquema, arquivo, res.problemas)
        _checar_unidades(nc, res.problemas)
        if padrao == "arm":
            from geracao_netcdf import padrao_arm
            res.problemas += padrao_arm.checar(nc, arquivo)
        elif padrao:
            raise ValueError(f"padrão desconhecido: {padrao!r} (use 'arm')")
        if referencia is not None:
            ref = _arquivo_referencia(Path(referencia), arquivo)
            if ref is None:
                res.problemas.append(Problema("R_SEM_REFERENCIA", "aviso",
                                              f"sem arquivo de referência para {arquivo.name}"))
            else:
                res.comparacao = comparar(nc, ref, tol_rel, tol_abs, res.problemas)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("arquivos", nargs="+")
    ap.add_argument("--aparelho", help="usa o esquema do aparelho registrado (ex.: rd80)")
    ap.add_argument("--esquema", help="netCDF_info.json")
    ap.add_argument("--referencia", help="NetCDF ou pasta com os arquivos-gabarito")
    ap.add_argument("--tol-rel", type=float, default=1e-5)
    ap.add_argument("--json", help="grava o relatório completo neste arquivo")
    ap.add_argument("--curto", action="store_true", help="mostra só os erros")
    ap.add_argument("--padrao", choices=["arm"],
                    help="acrescenta o checklist do ARM Data File Standards 1.3")
    a = ap.parse_args()
    esquema = None
    if a.esquema:
        esquema = carregar_esquema(a.esquema)
    elif a.aparelho:
        from geracao_netcdf.aparelhos import caminho_do_aparelho
        esquema = carregar_esquema(caminho_do_aparelho(a.aparelho) / "netCDF_info.json")
    resultados = [validar(f, esquema, a.referencia, a.tol_rel, padrao=a.padrao)
                  for f in a.arquivos]
    for r in resultados:
        print(r.texto(curto=a.curto))
    if a.json:
        Path(a.json).write_text(json.dumps([r.to_dict() for r in resultados],
                                           ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(0 if all(r.aprovado for r in resultados) else 1)


if __name__ == "__main__":
    main()
