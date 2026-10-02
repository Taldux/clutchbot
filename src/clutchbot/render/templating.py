"""Jinja2 for card templates"""

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

TEMPLATES_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    # names come from lobbies and profiles, so every value is HTML-escaped
    autoescape=select_autoescape(["html"]),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_template(name: str, **context: Any) -> str:
    return _env.get_template(name).render(**context)
