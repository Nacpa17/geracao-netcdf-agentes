"""Servidor ARM FALSO para testes: responde nos MESMOS formatos que o ACT
(biblioteca oficial da ARM) espera dos serviços reais. Dados inventados."""

from __future__ import annotations

import json
from urllib.parse import urlencode

DOD_1TWRMR_C1 = {"versions": {
    "1.0": {"atts": [{"name": "datastream", "value": "sgp1twrmrC1.c1"}],
            "dims": [{"name": "time", "length": 0}],
            "vars": [{"name": "temp", "dims": ["time"], "atts": [
                {"name": "long_name", "value": "Air temperature"},
                {"name": "units", "value": "K"}]}]},
    "1.1": {"atts": [{"name": "datastream", "value": "sgp1twrmrC1.c1"},
                     {"name": "doi", "value": "10.5439/0000001"},
                     {"name": "command_line", "value": "twrmr -s sgp"}],
            "dims": [{"name": "time", "length": 0}],
            "vars": [
                {"name": "mixing_ratio", "dims": ["time"], "atts": [
                    {"name": "long_name", "value": "Water vapor mixing ratio"},
                    {"name": "units", "value": "g/kg"}]},
                {"name": "rh", "dims": ["time"], "atts": [
                    {"name": "long_name", "value": "Relative humidity"},
                    {"name": "units", "value": "%"}]},
                {"name": "temp", "dims": ["time"], "atts": [
                    {"name": "long_name", "value": "Air temperature"},
                    {"name": "units", "value": "degC"}]},
                {"name": "qc_rh", "dims": ["time"], "atts": [
                    {"name": "long_name",
                     "value": "Quality check results on field: Relative humidity"},
                    {"name": "units", "value": "unitless"}]},
                {"name": "station_id", "dims": [], "atts": [
                    {"name": "string", "value": None},
                    {"name": "long_name", "value": "Station identifier"},
                    {"name": "units", "value": "unitless"}]},
            ]},
}}

DOD_KAZRGE_A1 = {"versions": {"2.0": {"atts": [], "dims": [], "vars": [
    {"name": "reflectivity", "dims": ["time", "range"], "atts": [
        {"name": "long_name", "value": "Equivalent reflectivity factor"},
        {"name": "units", "value": "dBZ"}]},
    {"name": "mean_doppler_velocity", "dims": ["time", "range"], "atts": [
        {"name": "long_name", "value": "Mean Doppler velocity"},
        {"name": "units", "value": "m/s"}]},
]}}}

DQR_SGP1TWRMR = {"sgp1twrmrC1.c1": {
    "Suspect": {"D190101.1": {
        "subject": "Humidity sensor drift at 60 m",
        "description": "The relative humidity sensor at 60 m drifted high.",
        "suggestions": "Use the 25 m level for comparisons.",
        "variables": ["rh", "mixing_ratio"],
        "dates": [{"start_date": "2019-01-01 00:00:00",
                   "end_date": "2019-02-01 00:00:00"}]}},
    "Incorrect": {"D200505.2": {
        "subject": "Logger clock error",
        "description": "Timestamps were offset by one hour.",
        "suggestions": None,
        "variables": ["temp"],
        "dates": [{"start_date": "2020-05-05 00:00:00",
                   "end_date": "2020-05-06 00:00:00"}]}},
}}

CITATION_SGP1TWRMR = {"citation": (
    "Atmospheric Radiation Measurement (ARM) user facility. 1998. Sixty Meter "
    "Tower (1TWRMR). Southern Great Plains (SGP) Central Facility (C1). "
    "ARM Data Center. Data set accessed at http://dx.doi.org/10.5439/0000001.")}

SITES_SGP = {"aggregations": {"distinct_facility_code": {"buckets": [
    {"key": "C1", "hits": {"hits": {"hits": [{"_source": {
        "site_code": "sgp", "facility_code": "C1", "site_type": "Fixed",
        "location": "36.607322,-97.487643"}}]}}},
    {"key": "E13", "hits": {"hits": {"hits": [{"_source": {
        "site_code": "sgp", "facility_code": "E13", "site_type": "Fixed",
        "location": None}}]}}},
]}}}


class FakeResponse:
    def __init__(self, status: int, payload=None, text: str | None = None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else (
            json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            return json.loads(self.text)
        return self._payload


class FakeArmSession:
    """Roteia URLs para as respostas acima e registra as chamadas."""

    def __init__(self, overrides: dict[str, list[FakeResponse]] | None = None):
        self.headers: dict = {}
        self.calls: list[tuple[str, str]] = []
        self.overrides = overrides or {}

    def _route(self, method: str, url: str, params=None, body=None) -> FakeResponse:
        full = url + (("?" + urlencode(params)) if params else "")
        self.calls.append((method, full))
        for prefix, queue in self.overrides.items():
            if full.startswith(prefix) and queue:
                return queue.pop(0)
        if url == "https://pcm.arm.gov/pcm/api/dods/1twrmr.c1":
            return FakeResponse(200, DOD_1TWRMR_C1)
        if url == "https://pcm.arm.gov/pcm/api/dods/kazrge.a1":
            return FakeResponse(200, DOD_KAZRGE_A1)
        if url.startswith("https://dqr-web-service.arm.gov/dqr_full/sgp1twrmrC1.c1/"):
            return FakeResponse(200, DQR_SGP1TWRMR)
        if url.startswith("https://adc.arm.gov/citationservice/") and \
                (params or {}).get("id") == "sgp1twrmrC1.c1":
            return FakeResponse(200, CITATION_SGP1TWRMR)
        if url == "https://adc.arm.gov/elastic/metadata/_search" and \
                "site_code:sgp" in json.dumps(body):
            return FakeResponse(200, SITES_SGP)
        return FakeResponse(404, text="")

    def get(self, url, params=None, timeout=None):
        return self._route("GET", url, params=params)

    def post(self, url, params=None, json=None, timeout=None):
        return self._route("POST", url, params=params, body=json)
