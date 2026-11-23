"""Clientes para serviços OFICIAIS da ARM além do JSON-LD da Data Discovery.

Os endpoints e formatos abaixo são os mesmos usados pelo ACT (Atmospheric
data Community Toolkit, biblioteca oficial da ARM em Python —
https://github.com/ARM-DOE/ACT, versão 2.3.4), de onde copiamos o formato das
requisições e das respostas. Nenhum deles exige login:

- DOD (Data Object Definition): estrutura oficial de cada produto — variáveis
  com nome longo (long_name) e unidade (units), dimensões e versões.
    GET https://pcm.arm.gov/pcm/api/dods/<classe>.<nível>      ex.: kazrge.a1
    -> {"versions": {"1.0": {"atts": [...], "dims": [...], "vars": [...]}}}

- DQR (Data Quality Report): problemas de qualidade conhecidos nos dados.
    GET https://dqr-web-service.arm.gov/dqr_full/<datastream>/<AAAAMMDD>/<AAAAMMDD>/<categorias>
    -> {"<datastream>": {"<categoria>": {"<id>": {"subject", "description",
        "suggestions", "variables", "dates": [{"start_date", "end_date"}]}}}}
    (sem relatórios: HTTP 404 ou JSON sem a chave do datastream)

- Citação oficial (com DOI) de um datastream num período:
    GET https://adc.arm.gov/citationservice/citation/datastream
        ?id=<datastream>&citationType=apa&startDate=...&endDate=...
    -> {"citation": "..."}

- Índice de metadados da ARM Data Discovery (Elasticsearch):
    POST https://adc.arm.gov/elastic/metadata/_search
    -> usamos a MESMA consulta do ACT (arm_site_location_search): sites e
       facilities com coordenadas (site_code, facility_code, site_type,
       location="lat,lon").

O cliente é "educado": identifica o projeto no User-Agent, espera entre
requisições, repete em erros temporários e guarda as respostas em cache no
disco — uma segunda rodada não incomoda o servidor da ARM de novo.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import requests

from geracao_netcdf.utils import setup_logger

log = setup_logger("geracao_netcdf.arm_services")

DOD_URL = "https://pcm.arm.gov/pcm/api/dods/"
DQR_URL = "https://dqr-web-service.arm.gov/dqr_full"
DQR_PAGE_URL = "https://adc.arm.gov/ArchiveServices/DQRService?dqrid="
CITATION_URL = "https://adc.arm.gov/citationservice/citation/datastream"
METADATA_SEARCH_URL = "https://adc.arm.gov/elastic/metadata/_search"

DEFAULT_USER_AGENT = "INPE-IC-Researcher/1.0 (mailto:eduardoamparo@usp.br)"

# Categorias de DQR aceitas pelo serviço (docstring do ACT).
DQR_ASSESSMENTS = ("incorrect", "suspect", "missing")


class ArmServiceError(RuntimeError):
    """Falha não recuperável ao consultar um serviço da ARM."""


class ArmClient:
    """GET/POST JSON com cache em disco, espera entre chamadas e retentativas.

    Respostas 404 também vão para o cache (como "não existe"), para não
    perguntar de novo pelo mesmo produto inexistente.
    """

    def __init__(self, cache_dir: str | Path | None = None,
                 user_agent: str = DEFAULT_USER_AGENT, delay_s: float = 0.5,
                 retries: int = 3, timeout_s: float = 30.0,
                 refresh: bool = False, session: Any = None):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay_s = delay_s
        self.retries = retries
        self.timeout_s = timeout_s
        self.refresh = refresh
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.n_requests = 0
        self.n_cache_hits = 0
        self._last = 0.0

    # ------------------------------------------------------------------ #
    def _key(self, method: str, url: str, params: Any, body: Any) -> str:
        raw = json.dumps([method, url, params, body], sort_keys=True,
                         ensure_ascii=False)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _cache_read(self, key: str) -> dict | None:
        if not self.cache_dir or self.refresh:
            return None
        p = self.cache_dir / f"{key}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
        return None

    def _cache_write(self, key: str, entry: dict) -> None:
        if self.cache_dir:
            (self.cache_dir / f"{key}.json").write_text(
                json.dumps(entry, ensure_ascii=False), encoding="utf-8")

    def _wait(self) -> None:
        dt = time.monotonic() - self._last
        if dt < self.delay_s:
            time.sleep(self.delay_s - dt)
        self._last = time.monotonic()

    def request_json(self, method: str, url: str, params: dict | None = None,
                     body: Any = None) -> Any | None:
        """JSON da resposta; None se o recurso não existe (404)."""
        key = self._key(method, url, params, body)
        cached = self._cache_read(key)
        if cached is not None:
            self.n_cache_hits += 1
            return cached.get("json") if cached.get("status") == 200 else None

        delay = 1.0
        last_err: str = ""
        for attempt in range(1, self.retries + 1):
            self._wait()
            self.n_requests += 1
            try:
                if method == "POST":
                    resp = self.session.post(url, params=params, json=body,
                                             timeout=self.timeout_s)
                else:
                    resp = self.session.get(url, params=params,
                                            timeout=self.timeout_s)
            except requests.RequestException as e:
                last_err = str(e)
                log.warning("Erro de rede em %s (tentativa %d/%d): %s",
                            url, attempt, self.retries, e)
                time.sleep(delay)
                delay *= 2
                continue
            if resp.status_code == 404:
                self._cache_write(key, {"url": url, "status": 404})
                return None
            if resp.status_code == 200:
                text = resp.text.strip()
                if not text:
                    self._cache_write(key, {"url": url, "status": 404})
                    return None
                try:
                    data = resp.json()
                except ValueError:
                    raise ArmServiceError(
                        f"Resposta não-JSON de {url} (início: {text[:80]!r})")
                self._cache_write(key, {"url": url, "status": 200,
                                        "json": data})
                return data
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {resp.status_code}"
                log.warning("%s em %s (tentativa %d/%d); aguardando %.0fs",
                            last_err, url, attempt, self.retries, delay)
                time.sleep(delay)
                delay *= 2
                continue
            raise ArmServiceError(f"HTTP {resp.status_code} em {url}")
        raise ArmServiceError(f"Esgotadas as tentativas em {url}: {last_err}")

    def get_json(self, url: str, params: dict | None = None) -> Any | None:
        return self.request_json("GET", url, params=params)

    def post_json(self, url: str, body: Any) -> Any | None:
        return self.request_json("POST", url, body=body)


# --------------------------------------------------------------------------- #
# DOD — definição oficial do produto (variáveis, unidades, dimensões)
# --------------------------------------------------------------------------- #
def fetch_dod(client: ArmClient, proc: str) -> dict | None:
    """DOD bruto de "<classe>.<nível>" (ex.: "kazrge.a1"), ou None."""
    return client.get_json(DOD_URL + proc)


def _atts(items: list | None) -> dict[str, Any]:
    """Lista [{"name", "value"}] -> dict, com a mesma regra do ACT:
    o marcador "string" indica variável de texto, cujo "units" é ignorado."""
    out: dict[str, Any] = {}
    str_flag = False
    for a in items or []:
        name = (a or {}).get("name")
        if not name:
            continue
        if name == "string":
            str_flag = True
            continue
        if a.get("value") is None or (str_flag and name == "units"):
            continue
        out[name] = a["value"]
    return out


def is_qc_variable(name: str) -> bool:
    return name.startswith(("qc_", "aqc_"))


def parse_dod(raw: dict | None, proc: str) -> dict | None:
    """Resumo do DOD na versão MAIS RECENTE (última chave, como o ACT)."""
    versions = (raw or {}).get("versions") or {}
    if not versions:
        return None
    vkey = list(versions)[-1]
    v = versions[vkey] or {}
    variables = []
    for var in v.get("vars") or []:
        name = (var or {}).get("name")
        if not name:
            continue
        atts = _atts(var.get("atts"))
        variables.append({
            "name": name,
            "long_name": atts.get("long_name"),
            "units": atts.get("units"),
            "standard_name": atts.get("standard_name"),
            "dims": list(var.get("dims") or []),
            "is_qc": is_qc_variable(name),
        })
    gatts = _atts(v.get("atts"))
    keep = ("datastream", "title", "description", "process_version",
            "doi", "site_id", "platform_id", "facility_id", "data_level",
            "location_description", "input_datastreams", "command_line")
    return {
        "proc": proc,
        "version": vkey,
        "n_versions": len(versions),
        "global_atts": {k: gatts[k] for k in keep if k in gatts},
        "dims": {d["name"]: d.get("length") for d in v.get("dims") or []
                 if d.get("name")},
        "variables": variables,
    }


# --------------------------------------------------------------------------- #
# DQR — relatórios de qualidade
# --------------------------------------------------------------------------- #
def fetch_dqrs(client: ArmClient, datastream: str, start: str, end: str,
               assessments: tuple[str, ...] = DQR_ASSESSMENTS) -> dict | None:
    """DQRs brutos; datas em AAAAMMDD. None = nenhum relatório."""
    cats = ",".join(a.strip().lower() for a in assessments)
    url = f"{DQR_URL}/{datastream}/{start}/{end}/{cats}"
    return client.get_json(url)


def parse_dqrs(raw: dict | None, datastream: str) -> list[dict]:
    docs = (raw or {}).get(datastream) or {}
    out = []
    for category, items in docs.items():
        for dqr_id, d in (items or {}).items():
            d = d or {}
            out.append({
                "id": str(dqr_id),
                "datastream": datastream,
                "category": str(category).lower(),
                "subject": d.get("subject"),
                "description": d.get("description"),
                "suggestions": d.get("suggestions"),
                "variables": list(d.get("variables") or []),
                "dates": [{"start": x.get("start_date"), "end": x.get("end_date")}
                          for x in d.get("dates") or [] if x],
                "url": DQR_PAGE_URL + str(dqr_id),
            })
    out.sort(key=lambda r: r["id"])
    return out


# --------------------------------------------------------------------------- #
# Citação oficial
# --------------------------------------------------------------------------- #
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>,;]+")


def fetch_citation(client: ArmClient, datastream: str, start: str,
                   end: str) -> str | None:
    """Citação APA oficial (datas AAAA-MM-DD), ou None."""
    data = client.get_json(CITATION_URL, params={
        "id": datastream, "citationType": "apa",
        "startDate": start, "endDate": end})
    cit = (data or {}).get("citation") if isinstance(data, dict) else None
    return cit.strip() if isinstance(cit, str) and cit.strip() else None


def doi_from_text(text: str | None) -> str | None:
    m = DOI_RE.search(text or "")
    return m.group(0).rstrip(".") if m else None


# --------------------------------------------------------------------------- #
# Sítios e facilities (índice de metadados da Data Discovery)
# --------------------------------------------------------------------------- #
def facilities_query(site_code: str) -> dict:
    """A mesma consulta de act.utils.data_utils.arm_site_location_search."""
    return {
        "aggs": {"distinct_facility_code": {
            "terms": {"field": "facility_code.keyword",
                      "order": {"_key": "asc"}, "size": 7000},
            "aggs": {"hits": {"top_hits": {
                "_source": ["site_type", "site_code", "facility_code",
                            "location"],
                "size": 1}}}}},
        "size": 0,
        "query": {"query_string": {"query": f"site_code:{site_code}"}},
    }


def fetch_facilities(client: ArmClient, site_code: str) -> list[dict]:
    raw = client.post_json(METADATA_SEARCH_URL, facilities_query(site_code))
    return parse_facilities(raw)


def parse_facilities(raw: dict | None) -> list[dict]:
    buckets = (((raw or {}).get("aggregations") or {})
               .get("distinct_facility_code") or {}).get("buckets") or []
    out = []
    for b in buckets:
        hits = (((b or {}).get("hits") or {}).get("hits") or {}).get("hits") or []
        if not hits:
            continue
        src = hits[0].get("_source") or {}
        lat = lon = None
        loc = src.get("location")
        if isinstance(loc, str) and "," in loc:
            try:
                lat, lon = (float(x) for x in loc.split(",", 1))
            except ValueError:
                lat = lon = None
        out.append({"site_code": src.get("site_code"),
                    "facility_code": src.get("facility_code"),
                    "site_type": src.get("site_type"),
                    "latitude": lat, "longitude": lon})
    return out
