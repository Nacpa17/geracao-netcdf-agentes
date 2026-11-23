"""Utilitários compartilhados pelos módulos do pacote."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import yaml


def setup_logger(name: str = "geracao_netcdf", level: str = "INFO") -> logging.Logger:
    """Configura um logger simples com formatação consistente."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Lê um arquivo YAML (UTF-8)."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
