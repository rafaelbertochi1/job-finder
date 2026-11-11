import hashlib
from dataclasses import dataclass, field


@dataclass
class Job:
    source: str
    external_id: str
    title: str
    url: str
    company: str = ""
    description: str = ""
    location: str = ""
    # remote | hybrid | onsite | unknown
    workplace: str = "unknown"
    posted_at: str = ""  # ISO 8601
    tags: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return hashlib.sha1(f"{self.source}:{self.external_id}".encode()).hexdigest()[:12]

    def as_prompt(self) -> str:
        return (
            f"Título: {self.title}\n"
            f"Empresa: {self.company or 'não informada'}\n"
            f"Fonte: {self.source}\n"
            f"Link: {self.url}\n"
            f"Modalidade (informada pela fonte): {self.workplace}\n"
            f"Local: {self.location or 'não informado'}\n"
            f"Publicada em: {self.posted_at or 'não informado'}\n"
            f"Tags: {', '.join(self.tags) or 'nenhuma'}\n\n"
            f"Descrição:\n{self.description}"
        )
