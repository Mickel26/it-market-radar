"""Profil kandydata: co umiesz, czego szukasz, czego nie chcesz.

Ten plik NIE trafia do repo. Repozytorium jest publiczne, a profil to dane
osobowe - `.gitignore` pilnuje katalogu `profile/`, a zaden kod tutaj nigdzie
go nie wysyla. Dopasowywanie dzieje sie w calosci lokalnie.

Profil mozna napisac recznie albo podpowiedziec sobie z dwoch zrodel:

    python -m radar.profile --from-linkedin ~/Downloads/Basic_LinkedInDataExport.zip
    python -m radar.profile --from-cv profile/cv.txt

LinkedIn: uzywamy OFICJALNEGO eksportu (Ustawienia -> Prywatnosc danych ->
Pobierz kopie swoich danych), a nie scrapowania. Scrapowanie LinkedIna lamie
jego regulamin i technicznie i tak nie dziala; eksport to te same dane,
legalnie i kompletniej.

CV: plik tekstowy. PDF-a wyeksportuj do .txt albo wklej tresc recznie -
projekt nie ma zaleznosci i nie bedzie mial parsera PDF-ow.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .queries import CONTRACT_TYPES, SENIORITIES, TECHNOLOGIES, WORK_MODES

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "profile"
DEFAULT_PROFILE = PROFILE_DIR / "profile.json"

# Slownik umiejetnosci, ktorych szukamy w CV. Zaczyna sie od technologii
# mierzonych przez radar, bo to wlasnie z nimi bedziemy dopasowywac oferty,
# i dokladamy rzeczy, ktore w ogloszeniach padaja stale, a nie sa osobnym
# wymiarem pomiarowym.
EXTRA_SKILLS = [
    "Git", "HTML", "CSS", "SCSS", "REST API", "GraphQL", "Redux",
    "MySQL", "MongoDB", "Redis", "Elasticsearch", "Kafka", "RabbitMQ",
    "Jenkins", "GitLab CI", "GitHub Actions", "Ansible", "Bash", "PowerShell",
    "Jira", "Scrum", "Agile", "Figma", "Excel", "Tableau", "Pandas", "NumPy",
    "Angielski", "Niemiecki",
]

SKILL_VOCABULARY: list[str] = sorted(
    {name for names in TECHNOLOGIES.values() for name in names} | set(EXTRA_SKILLS)
)

# Nazwy tej samej technologii, ktore w ogloszeniach padaja wymiennie.
# Klucz to forma znormalizowana, wartosc to forma kanoniczna.
ALIASES: dict[str, str] = {
    "reactjs": "react",
    "react.js": "react",
    "nodejs": "node.js",
    "node": "node.js",
    "vuejs": "vue",
    "vue.js": "vue",
    "nextjs": "next.js",
    "ts": "typescript",
    "js": "javascript",
    "es6": "javascript",
    "postgres": "postgresql",
    "psql": "postgresql",
    "k8s": "kubernetes",
    "dotnet": ".net",
    ".net core": ".net",
    "asp.net": ".net",
    "csharp": "c#",
    "golang": "go",
    "ci": "ci/cd",
    "cd": "ci/cd",
    "continuous integration": "ci/cd",
    "tailwindcss": "tailwind",
    "restapi": "rest api",
    "rest": "rest api",
    "english": "angielski",
    "german": "niemiecki",
}


def normalize(skill: str) -> str:
    """Sprowadza nazwe umiejetnosci do jednej postaci, zeby dalo sie porownywac."""
    text = (skill or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" .,;:()[]")
    return ALIASES.get(text, text)


@dataclass
class Profile:
    """Czego szukasz. Wszystko opcjonalne poza `skills`."""

    skills: list[str] = field(default_factory=list)
    # Rzeczy, ktorych sie wlasnie uczysz - licza sie, ale z mniejsza waga.
    learning: list[str] = field(default_factory=list)
    seniority: list[str] = field(default_factory=lambda: ["intern", "junior"])
    work_modes: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    salary_min: int | None = None
    exclude_companies: list[str] = field(default_factory=list)
    exclude_skills: list[str] = field(default_factory=list)

    # --- postaci znormalizowane, liczone raz ---

    @property
    def have(self) -> set[str]:
        return {normalize(s) for s in self.skills if s.strip()}

    @property
    def learning_set(self) -> set[str]:
        return {normalize(s) for s in self.learning if s.strip()} - self.have

    @property
    def unwanted(self) -> set[str]:
        return {normalize(s) for s in self.exclude_skills if s.strip()}

    @property
    def unwanted_companies(self) -> set[str]:
        return {c.strip().lower() for c in self.exclude_companies if c.strip()}

    @classmethod
    def load(cls, path: Path = DEFAULT_PROFILE) -> "Profile":
        if not path.exists():
            raise FileNotFoundError(
                f"Brak profilu: {path}\n"
                "Zacznij od szkieletu:  python -m radar.profile --init\n"
                "albo podpowiedz sobie: python -m radar.profile --from-cv profile/cv.txt"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(
                f"Nieznane pola w profilu: {', '.join(sorted(unknown))}. "
                f"Dozwolone: {', '.join(sorted(known))}"
            )
        profile = cls(**data)
        profile.validate()
        return profile

    def validate(self) -> None:
        """Literowka w 'junior' cicho wyzerowalaby wyniki - lepiej krzyknac."""
        for value in self.seniority:
            if value not in SENIORITIES:
                raise ValueError(f"Nieznany poziom: {value!r}. Dozwolone: {SENIORITIES}")
        for value in self.work_modes:
            if value not in WORK_MODES:
                raise ValueError(f"Nieznany tryb pracy: {value!r}. Dozwolone: {WORK_MODES}")
        for value in self.contracts:
            if value not in CONTRACT_TYPES:
                raise ValueError(f"Nieznany typ umowy: {value!r}. Dozwolone: {CONTRACT_TYPES}")
        if not self.skills:
            raise ValueError("Profil bez ani jednej umiejetnosci nie dopasuje niczego.")

    def save(self, path: Path = DEFAULT_PROFILE) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "skills": self.skills,
            "learning": self.learning,
            "seniority": self.seniority,
            "work_modes": self.work_modes,
            "contracts": self.contracts,
            "salary_min": self.salary_min,
            "exclude_companies": self.exclude_companies,
            "exclude_skills": self.exclude_skills,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


# --- podpowiadanie umiejetnosci -------------------------------------------


# Polskie koncowki fleksyjne: w CV pisze sie "w Scrumie", "w Excelu",
# "w Pythonie", a nie mianownikiem.
INFLECTION = r"(?:a|em|ie|ie?m|u|y|i|ow|om|ach|ami|owi|owe|owa|owy)?"


def skills_from_text(text: str, vocabulary: Iterable[str] = SKILL_VOCABULARY) -> list[str]:
    """Wyszukuje w tekscie nazwy ze slownika umiejetnosci.

    Dopasowanie po granicy slowa, zeby "Go" nie lapalo sie w "Google", a "R"
    w kazdym zdaniu. Nazwy z kropkami i plusami sa escape'owane.

    Nazwom dluzszym niz 3 znaki i czysto literowym pozwalamy na polska
    koncowke ("Scrumie" -> Scrum). Krotkie i te ze znakami specjalnymi
    ("Go", "C#", ".NET") zostaja przy scislym dopasowaniu, bo to wlasnie one
    generuja falszywe trafienia.
    """
    found = []
    for skill in vocabulary:
        inflects = len(skill) > 3 and skill.isalpha()
        stems = [re.escape(skill)]
        if inflects and skill.lower().endswith("a"):
            # "Java" -> "Javie", "Javy": polska odmiana ucina koncowe -a ze rdzenia,
            # wiec samo doklejanie koncowek tego nie zlapie.
            stems.append(re.escape(skill[:-1]) + r"(?:y|ie|e|o|ą|ę)")
        suffix = INFLECTION if inflects else ""

        # \b nie dziala po prawej stronie znaku niealfanumerycznego (C#, Next.js),
        # wiec prawa granice sprawdzamy recznie.
        pattern = rf"(?<![\w#+.])(?:{'|'.join(stems)}){suffix}(?![\w#+])"
        if re.search(pattern, text, flags=re.IGNORECASE):
            found.append(skill)
    return found


def skills_from_cv(path: Path) -> list[str]:
    """Umiejetnosci wychwycone z CV zapisanego jako tekst."""
    if path.suffix.lower() == ".pdf":
        raise SystemExit(
            f"{path} to PDF. Projekt nie ma zaleznosci, wiec nie parsuje PDF-ow.\n"
            "Wyeksportuj CV do .txt (w Wordzie: Zapisz jako -> Zwykly tekst) "
            "albo wklej tresc do pliku tekstowego."
        )
    return skills_from_text(path.read_text(encoding="utf-8", errors="replace"))


def skills_from_linkedin(zip_path: Path) -> list[str]:
    """Umiejetnosci z oficjalnego eksportu danych z LinkedIna.

    W paczce interesuje nas Skills.csv (lista umiejetnosci) i Positions.csv
    (opisy stanowisk, w ktorych technologie tez padaja).
    """
    found: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        names = {name.rsplit("/", 1)[-1].lower(): name for name in archive.namelist()}

        if skills_file := names.get("skills.csv"):
            with archive.open(skills_file) as handle:
                reader = csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8", errors="replace"))
                for row in reader:
                    # LinkedIn nazywa te kolumne "Name"; bierzemy pierwsza niepusta.
                    value = next((v for v in row.values() if v and v.strip()), "")
                    if value.strip():
                        found.append(value.strip())

        if positions_file := names.get("positions.csv"):
            with archive.open(positions_file) as handle:
                text = io.TextIOWrapper(handle, encoding="utf-8", errors="replace").read()
            found.extend(skills_from_text(text))

    if not found:
        raise SystemExit(
            f"W {zip_path.name} nie ma ani Skills.csv, ani Positions.csv.\n"
            "Upewnij sie, ze to eksport z LinkedIna (Ustawienia -> Prywatnosc danych -> "
            "Pobierz kopie swoich danych) i ze wybrales dane profilowe."
        )

    # Deduplikacja po formie znormalizowanej, z zachowaniem pierwszego zapisu.
    seen: dict[str, str] = {}
    for skill in found:
        seen.setdefault(normalize(skill), skill)
    return sorted(seen.values(), key=str.lower)


# --- CLI ------------------------------------------------------------------


SKELETON = Profile(
    skills=["SQL", "JavaScript", "Git"],
    learning=["TypeScript", "React"],
    seniority=["intern", "junior"],
    work_modes=["remote", "hybrid"],
    contracts=["employment_contract", "b2b"],
    salary_min=None,
    exclude_companies=[],
    exclude_skills=[],
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Buduje profil kandydata. Plik zostaje lokalnie, nie trafia do repo."
    )
    parser.add_argument("--init", action="store_true", help="zapisz szkielet profilu do wypelnienia")
    parser.add_argument("--from-cv", type=Path, metavar="PLIK", help="podpowiedz umiejetnosci z CV (.txt)")
    parser.add_argument(
        "--from-linkedin", type=Path, metavar="ZIP", help="podpowiedz umiejetnosci z eksportu LinkedIna"
    )
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_PROFILE)
    args = parser.parse_args(argv)

    if args.init:
        if args.output.exists():
            print(f"{args.output} juz istnieje - nie nadpisuje.")
            return 1
        path = SKELETON.save(args.output)
        print(f"Szkielet profilu: {path}\nWypelnij go i odpal: python -m radar.match")
        return 0

    suggested: list[str] = []
    if args.from_cv:
        suggested.extend(skills_from_cv(args.from_cv))
    if args.from_linkedin:
        suggested.extend(skills_from_linkedin(args.from_linkedin))

    if not suggested:
        parser.print_help()
        return 1

    seen: dict[str, str] = {}
    for skill in suggested:
        seen.setdefault(normalize(skill), skill)
    unique = sorted(seen.values(), key=str.lower)

    print(f"Znalezione umiejetnosci ({len(unique)}):\n")
    for skill in unique:
        print(f"  {skill}")
    print(
        "\nTo jest PODPOWIEDZ, nie gotowy profil - wyszukiwarka po frazie lapie tez"
        "\nrzeczy, ktorych tak naprawde nie umiesz. Przejrzyj liste, wytnij nadmiar,"
        "\ndopisz to, czego nie znalazla, i wklej do 'skills' w pliku profilu."
    )
    if args.output.exists():
        print(f"\nProfil: {args.output}")
    else:
        print(f"\nNie masz jeszcze profilu - zacznij od: python -m radar.profile --init")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
