from pathlib import Path

import yaml


class FaqService:
    def __init__(self, path: Path):
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self._faqs = data.get("faqs", [])

    def answer(self, text: str) -> str | None:
        for f in self._faqs:
            if any(k in (text or "") for k in f.get("keywords", [])):
                return f["answer"]
        return None
