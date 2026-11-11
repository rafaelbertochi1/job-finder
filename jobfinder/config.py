from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
PROFILE_PATH = DATA_DIR / "profile.json"
DB_PATH = DATA_DIR / "jobs.db"
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"


def load_config() -> dict:
    load_dotenv(ROOT / ".env")
    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _abs(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def all_cvs(cfg: dict) -> list[dict]:
    """Lista de currículos: [{path: Path, track, language}]. Aceita o formato antigo (cv_path)."""
    if "cvs" in cfg:
        return [{**c, "path": _abs(c["path"])} for c in cfg["cvs"]]
    return [{"path": _abs(cfg["cv_path"]), "track": "geral", "language": "pt"}]


def tracks(cfg: dict) -> list[str]:
    return list(dict.fromkeys(c["track"] for c in all_cvs(cfg)))


def pick_cv(cfg: dict, track: str | None, language: str | None) -> Path:
    """Currículo mais adequado para a vaga: mesma área e idioma > mesma área > mesmo idioma > primeiro."""
    cvs = all_cvs(cfg)
    for match in (
        lambda c: c["track"] == track and c["language"] == language,
        lambda c: c["track"] == track,
        lambda c: c["language"] == language,
        lambda c: True,
    ):
        for c in cvs:
            if match(c):
                return c["path"]
    raise ValueError("Nenhum currículo configurado em 'cvs' no config.yaml.")
