"""Definicja macierzy zapytan, czyli co wlasciwie mierzymy.

Trzymamy to w osobnym pliku, bo to jedyna czesc projektu, ktora bedzie
sie czesto zmieniac - dochodza nowe technologie, odchodza stare. Kod
zbierajacy dane nie powinien sie przez to ruszac.
"""

from __future__ import annotations

from typing import Iterator, NamedTuple

# Technologie pogrupowane po obszarze. Grupa sluzy tylko do prezentacji,
# zapytanie idzie po nazwie.
TECHNOLOGIES: dict[str, list[str]] = {
    "frontend": [
        "React", "Angular", "Vue", "Next.js", "TypeScript",
        "JavaScript", "Svelte", "Tailwind",
    ],
    "backend": [
        "Java", "Python", "C#", ".NET", "Node.js", "PHP", "Go",
        "Spring", "Django", "Laravel", "Rust", "Kotlin",
    ],
    "data": [
        "SQL", "PostgreSQL", "Power BI", "Snowflake", "Spark", "dbt",
    ],
    "devops": [
        "Docker", "Kubernetes", "AWS", "Azure", "Terraform", "CI/CD", "Linux",
    ],
    "qa": [
        "Selenium", "Playwright", "Cypress", "Jest",
    ],
    "ai": [
        "Machine Learning", "LLM", "PyTorch",
    ],
}

SENIORITIES = ["intern", "junior", "mid", "senior"]
WORK_MODES = ["remote", "hybrid", "office"]
CONTRACT_TYPES = ["b2b", "employment_contract", "internship"]

# Okno obserwacji. 30 dni daje stabilne liczby; przy 7d szum jest za duzy,
# zeby porownywac tygodnie miedzy soba.
DEFAULT_WINDOW = "30d"


class Query(NamedTuple):
    """Pojedyncza komorka macierzy pomiarowej."""

    kind: str          # "tech" | "seniority" | "work_mode" | "contract" | "total"
    label: str         # czytelna nazwa, np. "React"
    group: str | None  # np. "frontend"
    seniority: str | None
    arguments: dict    # to, co leci do search_jobs


def build_matrix(
    window: str = DEFAULT_WINDOW,
    seniorities: list[str] | None = None,
    technologies: dict[str, list[str]] | None = None,
) -> list[Query]:
    """Buduje pelna liste zapytan dla jednego przebiegu zbierania."""
    seniorities = seniorities or SENIORITIES
    technologies = technologies or TECHNOLOGIES
    return list(_iter_matrix(window, seniorities, technologies))


def _iter_matrix(
    window: str,
    seniorities: list[str],
    technologies: dict[str, list[str]],
) -> Iterator[Query]:
    base = {"postedWithin": window, "sortOrder": "newest"}

    # 1. Punkt odniesienia: caly rynek w oknie.
    yield Query("total", "rynek ogolem", None, None, dict(base))

    # 2. Struktura rynku wedlug poziomu doswiadczenia.
    for level in seniorities:
        yield Query("seniority", level, None, level, {**base, "seniority": [level]})

    # 3. Tryb pracy i typ kontraktu w przekroju poziomow.
    for level in seniorities:
        for mode in WORK_MODES:
            yield Query(
                "work_mode", mode, None, level,
                {**base, "seniority": [level], "workModes": [mode]},
            )
        for contract in CONTRACT_TYPES:
            yield Query(
                "contract", contract, None, level,
                {**base, "seniority": [level], "contractTypes": [contract]},
            )

    # 4. Serce radaru: technologia x poziom.
    for group, names in technologies.items():
        for name in names:
            for level in seniorities:
                yield Query(
                    "tech", name, group, level,
                    {**base, "phrase": f'"{name}"', "seniority": [level]},
                )
