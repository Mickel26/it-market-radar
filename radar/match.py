"""Dopasowywanie ofert do profilu - w calosci lokalnie.

    python -m radar.match                    # ranking na konsole
    python -m radar.match --html             # dodatkowo profile/matches.html
    python -m radar.match --limit 50 --min-score 40

Dziala na korpusie ofert z ostatniego snapshotu (data/snapshots/<data>/jobs.jsonl),
ktory NIE jest w repo - jest tylko na dysku tego, kto uruchomil radar.collect.
Nic stad nie wychodzi do sieci.

Czego ten modul NIE robi i nie bedzie robil: nie czyta LinkedIna przez
scrapowanie (lamie regulamin i nie dziala), nie wysyla Twojego profilu
nigdzie na zewnatrz, nie commituje wynikow.

O ocenie dopasowania
--------------------
Zamiast jednej nieprzejrzystej liczby pokazujemy dwie, bo mowia o czym innym:

* POKRYCIE - jaki procent wymagan oferty spelniasz. Odpowiada na "czy mam
  szanse".
* TRAFIENIA - ile Twoich umiejetnosci oferta faktycznie wymienia. Odpowiada
  na "czy to o mnie, czy przypadek".

Sama wysokie pokrycie potrafi klamac: oferta z jednym wymaganiem, ktore
akurat masz, to 100% pokrycia i zadna informacja. Dlatego wynik koncowy
przemnazamy przez nasycenie liczba trafien - pelny kredyt dopiero od trzech.
To ta sama zasada, co prog minimalnej liczby ofert na dashboardzie: maly
mianownik zmysla wyniki.
"""

from __future__ import annotations

import argparse
import html
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .analyze import DATA_DIR, Snapshot
from .profile import DEFAULT_PROFILE, PROFILE_DIR, Profile, normalize

# Od ilu trafien oferta dostaje pelny kredyt za dopasowanie.
FULL_CREDIT_HITS = 3
# Umiejetnosc "w trakcie nauki" liczy sie za pol.
LEARNING_WEIGHT = 0.5


@dataclass
class Match:
    job: dict[str, Any]
    matched: list[str]
    partial: list[str]
    missing: list[str]
    coverage: float  # 0-100
    score: float     # 0-100

    @property
    def title(self) -> str:
        for key in ("title", "position", "name"):
            if value := (self.job.get(key) or "").strip():
                return value
        return "(oferta bez tytulu)"

    @property
    def company(self) -> str:
        return (self.job.get("company") or "—").strip()

    @property
    def url(self) -> str:
        return (self.job.get("url") or "").strip()

    @property
    def seniority(self) -> str:
        return self.job.get("seniority") or "bez poziomu"

    @property
    def salary(self) -> str:
        low, high = self.job.get("salary_from"), self.job.get("salary_to")
        if low and high:
            return f"{int(low):,} - {int(high):,} zl".replace(",", " ")
        if low:
            return f"od {int(low):,} zl".replace(",", " ")
        if high:
            return f"do {int(high):,} zl".replace(",", " ")
        return "nie podano"


def job_requirements(job: dict[str, Any]) -> dict[str, str]:
    """Wymagania oferty: {postac znormalizowana: zapis z ogloszenia}.

    Porownujemy po znormalizowanej, ale pokazujemy zapis oryginalny - "Power BI"
    czyta sie lepiej niz "power bi", a "React.js" niesie informacje, ktorej
    "react" juz nie ma.
    """
    seen: dict[str, str] = {}
    for keyword in job.get("keywords") or []:
        raw = (keyword or "").strip()
        if raw:
            seen.setdefault(normalize(raw), raw)
    return seen


def plural_offers(count: int) -> str:
    """Polska odmiana: 1 oferta, 2 oferty, 5 ofert, 22 oferty, 25 ofert."""
    if count == 1:
        return "oferta"
    tens, units = count % 100, count % 10
    if 2 <= units <= 4 and not 12 <= tens <= 14:
        return "oferty"
    return "ofert"


def score_job(job: dict[str, Any], profile: Profile) -> Match | None:
    """Ocenia jedna oferte. Zwraca None, jesli odpada na twardym filtrze."""
    requirements = job_requirements(job)
    if not requirements:
        # Bez wymagan nie ma czego dopasowywac - taka oferta to szum, nie trafienie.
        return None

    required = set(requirements)
    have = profile.have
    learning = profile.learning_set

    if required & profile.unwanted:
        return None

    # Porownanie po formie znormalizowanej, prezentacja w zapisie z ogloszenia.
    def as_written(keys: set[str]) -> list[str]:
        return sorted((requirements[k] for k in keys), key=str.lower)

    matched = as_written(required & have)
    if not matched:
        return None

    partial = as_written(required & learning)
    missing = as_written(required - have - learning)

    coverage = (len(matched) + LEARNING_WEIGHT * len(partial)) / len(required)
    saturation = min(1.0, len(matched) / FULL_CREDIT_HITS)

    return Match(
        job=job,
        matched=matched,
        partial=partial,
        missing=missing,
        coverage=round(100 * coverage, 1),
        score=round(100 * coverage * saturation, 1),
    )


def passes_filters(job: dict[str, Any], profile: Profile) -> bool:
    """Twarde filtry, ktore nie maja nic wspolnego z umiejetnosciami."""
    if profile.seniority:
        level = job.get("seniority")
        # Oferte bez przypisanego poziomu przepuszczamy: agregator nie klasyfikuje
        # okolo 7% ogloszen, a odrzucanie ich po cichu ucinaloby realne szanse.
        if level and level not in profile.seniority:
            return False

    if (job.get("company") or "").strip().lower() in profile.unwanted_companies:
        return False

    if profile.salary_min:
        # Widelki ujawnia okolo polowy ofert. Brak widelek NIE dyskwalifikuje -
        # inaczej odrzucilibysmy polowe rynku za milczenie.
        top = job.get("salary_to") or job.get("salary_from")
        if top and top < profile.salary_min:
            return False

    return True


def find_matches(
    jobs: Iterable[dict[str, Any]],
    profile: Profile,
    *,
    min_score: float = 0.0,
) -> list[Match]:
    matches = []
    for job in jobs:
        if not passes_filters(job, profile):
            continue
        if (match := score_job(job, profile)) and match.score >= min_score:
            matches.append(match)

    # Malejaco po wyniku; przy remisie wyzej ta, ktora wymaga mniej douczenia.
    matches.sort(key=lambda m: (-m.score, len(m.missing), m.company))
    return matches


# --- prezentacja ----------------------------------------------------------


def print_matches(matches: list[Match], profile: Profile, limit: int, corpus_size: int) -> None:
    print(f"\n=== DOPASOWANIA ===\n")
    print(f"Korpus: {corpus_size} {plural_offers(corpus_size)} | po filtrach i dopasowaniu: {len(matches)}")
    print(f"Profil: {len(profile.have)} umiejetnosci, {len(profile.learning_set)} w nauce")
    print(f"Poziomy: {', '.join(profile.seniority) or 'dowolny'}\n")

    if not matches:
        print("Nic nie pasuje. Mozliwe przyczyny:")
        print("  - profil ma za malo umiejetnosci albo nazwy nie zgadzaja sie z ogloszeniami")
        print("  - filtry sa za ostre (poziom, salary_min, exclude_*)")
        print("  - snapshot jest stary - odswiez: python -m radar.collect")
        return

    for index, match in enumerate(matches[:limit], start=1):
        print(f"{index:>3}. [{match.score:>5.1f}] {match.title}")
        print(f"     {match.company} | {match.seniority} | {match.salary}")
        print(f"     pokrycie {match.coverage:.0f}% | masz: {', '.join(match.matched)}")
        if match.partial:
            print(f"     w nauce: {', '.join(match.partial)}")
        if match.missing:
            print(f"     brakuje: {', '.join(match.missing)}")
        if match.url:
            print(f"     {match.url}")
        print()

    if len(matches) > limit:
        print(f"...i {len(matches) - limit} dalszych (--limit {len(matches)}, zeby zobaczyc wszystkie)")

    gaps = gap_ranking(matches)
    if gaps:
        print("\n--- Czego brakuje najczesciej w ofertach, ktore i tak pasuja ---")
        print("(czyli co douczyc, zeby te same oferty staly sie mocniejsze)\n")
        for skill, count in gaps[:10]:
            print(f"  {skill:<20} {count:>3} {plural_offers(count)}")


def gap_ranking(matches: list[Match]) -> list[tuple[str, int]]:
    """Najczestsze braki wsrod dopasowanych ofert - to jest lista do nauki.

    Zliczamy po formie znormalizowanej, zeby "React" i "React.js" nie rozeszly
    sie na dwie pozycje, ale pokazujemy najpopularniejszy zapis z ogloszen.
    """
    counts: dict[str, int] = {}
    spellings: dict[str, dict[str, int]] = {}
    for match in matches:
        for skill in match.missing:
            key = normalize(skill)
            counts[key] = counts.get(key, 0) + 1
            spellings.setdefault(key, {})
            spellings[key][skill] = spellings[key].get(skill, 0) + 1

    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return [(max(spellings[key], key=spellings[key].get), count) for key, count in ranked]


def write_html(matches: list[Match], profile: Profile, path: Path, limit: int) -> Path:
    """Prosty plik do przegladania. Lezy w profile/, czyli poza repo."""
    rows = []
    for index, match in enumerate(matches[:limit], start=1):
        chips = "".join(f'<span class="s have">{html.escape(s)}</span>' for s in match.matched)
        chips += "".join(f'<span class="s learn">{html.escape(s)}</span>' for s in match.partial)
        chips += "".join(f'<span class="s miss">{html.escape(s)}</span>' for s in match.missing)
        link = (
            f'<a href="{html.escape(match.url)}">{html.escape(match.title)}</a>'
            if match.url
            else html.escape(match.title)
        )
        rows.append(
            f"<article><h2><span class=sc>{match.score:.0f}</span> {link}</h2>"
            f"<p class=meta>{html.escape(match.company)} · {html.escape(match.seniority)} · "
            f"{html.escape(match.salary)} · pokrycie {match.coverage:.0f}%</p>"
            f"<p class=chips>{chips}</p></article>"
        )

    gaps = "".join(f"<li>{html.escape(s)} <b>{c}</b></li>" for s, c in gap_ranking(matches)[:12])

    document = f"""<!DOCTYPE html>
<html lang=pl><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Dopasowane oferty</title>
<style>
:root{{color-scheme:light dark;--bg:#f9f9f7;--fg:#0b0b0b;--mut:#52514e;--card:#fcfcfb;--line:rgba(0,0,0,.1)}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0d0d0d;--fg:#fff;--mut:#c3c2b7;--card:#1a1a19;--line:rgba(255,255,255,.1)}}}}
body{{margin:0;padding:24px 16px 64px;background:var(--bg);color:var(--fg);
font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:760px;margin:0 auto}}
h1{{font-size:26px;margin:0 0 4px}}
.sub{{color:var(--mut);margin:0 0 28px}}
article{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:12px}}
h2{{font-size:16px;margin:0 0 4px;font-weight:600}}
h2 a{{color:inherit}}
.sc{{display:inline-block;min-width:34px;padding:1px 7px;margin-right:6px;border-radius:6px;
background:#2a78d6;color:#fff;font-size:13px;text-align:center;font-weight:700}}
.meta{{color:var(--mut);font-size:13px;margin:0 0 8px}}
.chips{{margin:0;display:flex;flex-wrap:wrap;gap:5px}}
.s{{font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--line)}}
.have{{background:rgba(12,163,12,.14)}}
.learn{{background:rgba(250,178,25,.18)}}
.miss{{opacity:.55}}
aside{{margin-top:32px;border-top:1px solid var(--line);padding-top:18px}}
aside ul{{columns:2;list-style:none;padding:0;color:var(--mut);font-size:14px}}
</style></head><body><main>
<h1>Dopasowane oferty</h1>
<p class=sub>{len(matches)} trafien z korpusu · profil: {len(profile.have)} umiejetnosci ·
wygenerowano {date.today().isoformat()}<br>
<span class="s have">masz</span> <span class="s learn">uczysz sie</span>
<span class="s miss">brakuje</span></p>
{"".join(rows)}
<aside><h2>Czego brakuje najczesciej</h2><ul>{gaps}</ul></aside>
</main></body></html>"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dopasowuje oferty z ostatniego snapshotu do Twojego profilu (lokalnie)."
    )
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--limit", type=int, default=25, help="ile ofert pokazac (domyslnie 25)")
    parser.add_argument("--min-score", type=float, default=0.0, help="odetnij slabe dopasowania")
    parser.add_argument("--html", action="store_true", help="zapisz takze profile/matches.html")
    parser.add_argument("--json", action="store_true", help="wypisz wynik jako JSON")
    args = parser.parse_args(argv)

    profile = Profile.load(args.profile)

    try:
        snapshot = Snapshot.latest(DATA_DIR)
    except FileNotFoundError as exc:
        print(exc)
        return 1

    if not snapshot.jobs:
        print(
            f"Snapshot {snapshot.day} nie ma korpusu ofert (jobs.jsonl).\n"
            "Korpus celowo nie trafia do repo, wiec w swiezym klonie go nie ma.\n"
            "Zbierz wlasny: python -m radar.collect"
        )
        return 1

    matches = find_matches(snapshot.jobs, profile, min_score=args.min_score)

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "score": m.score,
                        "coverage": m.coverage,
                        "title": m.title,
                        "company": m.company,
                        "url": m.url,
                        "seniority": m.seniority,
                        "matched": m.matched,
                        "partial": m.partial,
                        "missing": m.missing,
                    }
                    for m in matches[: args.limit]
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print_matches(matches, profile, args.limit, len(snapshot.jobs))

    if args.html:
        path = write_html(matches, profile, PROFILE_DIR / "matches.html", args.limit)
        print(f"\nHTML: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
