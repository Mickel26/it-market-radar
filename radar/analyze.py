"""Analiza snapshotu: zamienia surowe liczby w odpowiedzi na konkretne pytania.

Dwa zrodla, dwa rozne poziomy zaufania:

* measurements.jsonl - pole total_results, czyli PELNA liczba ofert pasujacych
  do zapytania. To sa liczby dokladne.
* jobs.jsonl - korpus ofert, ale tylko pierwsze 50 na zapytanie (serwer nie ma
  paginacji). To jest PROBA, nie populacja. Uzywamy jej do rozkladow
  (plac, wspolwystepowania technologii), nigdy do liczenia "ile jest ofert".

Mieszanie tych dwoch rzeczy to najlatwiejszy sposob, zeby skłamac wykresem,
wiec kazda metryka ma pole `source` mowiace, skad pochodzi.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Ile procent ofert w danym przekroju musi wymieniac technologie, zeby
# uznac ja za "realne wymaganie", a nie przypadek.
COOCCURRENCE_FLOOR = 0.15


@dataclass
class Snapshot:
    day: date
    measurements: list[dict[str, Any]]
    jobs: list[dict[str, Any]]
    manifest: dict[str, Any]

    @classmethod
    def load(cls, directory: Path) -> "Snapshot":
        return cls(
            day=date.fromisoformat(directory.name),
            measurements=_read_jsonl(directory / "measurements.jsonl"),
            jobs=_read_jsonl(directory / "jobs.jsonl"),
            manifest=json.loads((directory / "manifest.json").read_text(encoding="utf-8")),
        )

    @classmethod
    def latest(cls, data_dir: Path = DATA_DIR) -> "Snapshot":
        snapshots = sorted((data_dir / "snapshots").glob("[0-9]*-[0-9]*-[0-9]*"))
        if not snapshots:
            raise FileNotFoundError("Brak snapshotow - uruchom najpierw: python -m radar.collect")
        return cls.load(snapshots[-1])


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# --- metryki oparte na total_results (dokladne) ---------------------------


def market_structure(snapshot: Snapshot) -> dict[str, Any]:
    """Ile ofert na kazdym poziomie doswiadczenia i jaki to udzial rynku."""
    counts = {
        m["label"]: m["total_results"]
        for m in snapshot.measurements
        if m["kind"] == "seniority"
    }
    overall = next(
        (m["total_results"] for m in snapshot.measurements if m["kind"] == "total"),
        sum(counts.values()),
    )
    return {
        "source": "total_results",
        "total_offers": overall,
        "by_seniority": counts,
        "share_of_market": {
            level: round(100 * value / overall, 1) if overall else 0.0
            for level, value in counts.items()
        },
    }


def breakdown(snapshot: Snapshot, kind: str) -> dict[str, dict[str, int]]:
    """Rozklad work_mode albo contract w przekroju poziomow."""
    result: dict[str, dict[str, int]] = defaultdict(dict)
    for m in snapshot.measurements:
        if m["kind"] == kind and m["seniority"]:
            result[m["seniority"]][m["label"]] = m["total_results"]
    return dict(result)


def tech_demand(snapshot: Snapshot) -> list[dict[str, Any]]:
    """Liczba ofert per technologia i poziom + wskaznik otwartosci na juniorow.

    junior_ratio = oferty junior+intern / wszystkie oferty dla tej technologii.
    To jest wazniejsze niz sama popularnosc: Java ma duzo ofert, ale jesli
    99% to seniorzy, dla poczatkujacego jest to rynek zamkniety.
    """
    rows: dict[tuple[str, str | None], dict[str, Any]] = {}
    for m in snapshot.measurements:
        if m["kind"] != "tech":
            continue
        key = (m["label"], m["group"])
        row = rows.setdefault(key, {"technology": m["label"], "group": m["group"], "levels": {}})
        row["levels"][m["seniority"]] = m["total_results"]

    output = []
    for row in rows.values():
        levels = row["levels"]
        total = sum(levels.values())
        entry_level = levels.get("junior", 0) + levels.get("intern", 0)
        output.append(
            {
                "source": "total_results",
                "technology": row["technology"],
                "group": row["group"],
                "levels": levels,
                "total": total,
                "entry_level_offers": entry_level,
                "junior_ratio": round(100 * entry_level / total, 1) if total else 0.0,
            }
        )
    return sorted(output, key=lambda r: r["total"], reverse=True)


# --- metryki oparte na korpusie ofert (proba) -----------------------------


def _midpoint(job: dict[str, Any]) -> float | None:
    low, high = job.get("salary_from"), job.get("salary_to")
    if low and high:
        return (low + high) / 2
    return float(low or high) if (low or high) else None


def salary_stats(jobs: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Statystyki plac tam, gdzie widelki sa ujawnione."""
    by_level: dict[str, list[float]] = defaultdict(list)
    disclosed = Counter()
    seen = Counter()

    for job in jobs:
        level = job.get("seniority") or "nieznany"
        seen[level] += 1
        if (value := _midpoint(job)) is not None:
            by_level[level].append(value)
            disclosed[level] += 1

    result = {}
    for level, values in by_level.items():
        if len(values) < 3:
            continue
        values.sort()
        result[level] = {
            "sample_size": len(values),
            "disclosure_rate": round(100 * disclosed[level] / seen[level], 1),
            "median": round(statistics.median(values)),
            "p25": round(values[len(values) // 4]),
            "p75": round(values[(3 * len(values)) // 4]),
        }
    return {"source": "sample (max 50/zapytanie)", "by_seniority": result}


def cooccurrence(
    jobs: Iterable[dict[str, Any]],
    anchor: str,
    seniority: str | None = None,
    limit: int = 12,
) -> dict[str, Any]:
    """Czego jeszcze wymagaja oferty wymieniajace daną technologie.

    Odpowiada na pytanie "umiem X, czego douczyc sie nastepnie", ktore jest
    duzo bardziej uzyteczne niz ranking popularnosci technologii.
    """
    anchor_lower = anchor.lower()
    matching = [
        job for job in jobs
        if (seniority is None or job.get("seniority") == seniority)
        and any(anchor_lower in (k or "").lower() for k in job.get("keywords", []))
    ]
    if not matching:
        return {"anchor": anchor, "seniority": seniority, "sample_size": 0, "skills": []}

    counts: Counter[str] = Counter()
    for job in matching:
        # set(), zeby oferta wymieniajaca "React" i "React.js" nie liczyla sie dwa razy
        for keyword in {(k or "").strip() for k in job.get("keywords", [])}:
            if keyword and anchor_lower not in keyword.lower():
                counts[keyword] += 1

    total = len(matching)
    skills = [
        {"skill": name, "count": count, "share": round(100 * count / total, 1)}
        for name, count in counts.most_common(limit * 3)
        if count / total >= COOCCURRENCE_FLOOR
    ][:limit]

    return {
        "source": "sample (max 50/zapytanie)",
        "anchor": anchor,
        "seniority": seniority,
        "sample_size": total,
        "skills": skills,
    }


def top_hiring_companies(jobs: Iterable[dict[str, Any]], seniority: str | None = None, limit: int = 15):
    counts: Counter[str] = Counter()
    for job in jobs:
        if seniority and job.get("seniority") != seniority:
            continue
        if company := (job.get("company") or "").strip():
            counts[company] += 1
    return [{"company": name, "offers": count} for name, count in counts.most_common(limit)]


# --- raport ---------------------------------------------------------------


def build_report(snapshot: Snapshot, anchors: list[str] | None = None) -> dict[str, Any]:
    anchors = anchors or ["React", "Python", "Java", "SQL", "JavaScript"]
    demand = tech_demand(snapshot)

    return {
        "generated_for": snapshot.day.isoformat(),
        "window": snapshot.manifest.get("window"),
        "collection": {
            "queries_ok": snapshot.manifest.get("queries_ok"),
            "queries_failed": snapshot.manifest.get("queries_failed"),
            "jobs_in_corpus": len(snapshot.jobs),
        },
        "market": market_structure(snapshot),
        "work_modes": breakdown(snapshot, "work_mode"),
        "contracts": breakdown(snapshot, "contract"),
        "tech_demand": demand,
        "most_junior_friendly": sorted(
            [r for r in demand if r["entry_level_offers"] >= 5],
            key=lambda r: r["junior_ratio"],
            reverse=True,
        )[:12],
        "salaries": salary_stats(snapshot.jobs),
        "learning_paths": {
            anchor: cooccurrence(snapshot.jobs, anchor, seniority="junior") for anchor in anchors
        },
        "top_junior_employers": top_hiring_companies(snapshot.jobs, seniority="junior"),
    }


def print_summary(report: dict[str, Any]) -> None:
    market = report["market"]
    print(f"\n=== RYNEK IT W PL — okno {report['window']} (dane z {report['generated_for']}) ===\n")
    print(f"Ofert ogolem: {market['total_offers']}")
    for level, count in market["by_seniority"].items():
        share = market["share_of_market"].get(level, 0)
        print(f"  {level:<8} {count:>6}  ({share}% rynku)")

    print("\n--- Najwiecej ofert (wszystkie poziomy) ---")
    for row in report["tech_demand"][:12]:
        junior = row["levels"].get("junior", 0)
        print(f"  {row['technology']:<16} {row['total']:>6} ofert | junior: {junior:>4} ({row['junior_ratio']}%)")

    print("\n--- Najbardziej otwarte na poczatkujacych (min. 5 ofert entry) ---")
    for row in report["most_junior_friendly"][:10]:
        print(f"  {row['technology']:<16} {row['junior_ratio']:>5}% entry | {row['entry_level_offers']:>4} ofert junior/intern")

    print("\n--- Widelki (mediana, z ofert ktore je ujawniaja) ---")
    for level, stats in report["salaries"]["by_seniority"].items():
        print(
            f"  {level:<8} {stats['median']:>7} PLN  (p25 {stats['p25']} / p75 {stats['p75']}) "
            f"| ujawnia: {stats['disclosure_rate']}% | n={stats['sample_size']}"
        )

    print("\n--- Czego uczyc sie obok (oferty junior) ---")
    for anchor, data in report["learning_paths"].items():
        if not data["skills"]:
            continue
        top = ", ".join(f"{s['skill']} {s['share']}%" for s in data["skills"][:6])
        print(f"  {anchor} (n={data['sample_size']}): {top}")


def write_reports_index(reports_dir: Path) -> Path:
    """Spis raportow dla dashboardu — inaczej musialby miec date wpisana na sztywno."""
    days = sorted(p.stem for p in reports_dir.glob("*.json") if p.name != "index.json")
    index_path = reports_dir / "index.json"
    index_path.write_text(
        json.dumps({"reports": days, "latest": days[-1] if days else None}, indent=2),
        encoding="utf-8",
    )
    return index_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analizuje najnowszy snapshot rynku.")
    parser.add_argument("--anchors", default="React,Python,Java,SQL,JavaScript")
    parser.add_argument("--json", action="store_true", help="wypisz surowy raport JSON")
    args = parser.parse_args(argv)

    snapshot = Snapshot.latest()
    report = build_report(snapshot, anchors=[a.strip() for a in args.anchors.split(",") if a.strip()])

    out_path = DATA_DIR / "reports" / f"{snapshot.day.isoformat()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_reports_index(out_path.parent)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_summary(report)
        print(f"\nPelny raport: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
