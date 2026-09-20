/* Radar rynku pracy IT — dashboard.
 *
 * Czyta data/reports/<data>.json, czyli raport policzony offline przez
 * radar.analyze. Zadnej matematyki na rynku tutaj nie ma poza formatowaniem
 * i sortowaniem — jedyne, co ta warstwa robi z liczbami, to je rysuje.
 *
 * Surowy korpus ofert (jobs.jsonl) celowo nie trafia do repo, wiec dashboard
 * nigdy go nie potrzebuje.
 *
 * Kazda karta niesie znacznik zrodla: "dokladne" (total_results z serwera)
 * albo "proba" (<=50 ofert na zapytanie). Mieszanie tych dwoch poziomow
 * zaufania byloby najlatwiejszym sposobem, zeby sklamac wykresem.
 */

"use strict";

/* ---------------------------------------------------------------- pomocnicze */

// useGrouping "always", bo domyslnie Intl zostawia liczby czterocyfrowe bez
// spacji — "5218 ofert" stoi wtedy obok "12 500 ofert" i wyglada jak literowka
const NF = new Intl.NumberFormat("pl-PL", { useGrouping: "always" });
const SVG_NS = "http://www.w3.org/2000/svg";

const NF1 = new Intl.NumberFormat("pl-PL", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

const fmtInt = (n) => NF.format(n);
/** Zawsze jedno miejsce po przecinku — inaczej "6%" obok "37,3%" czyta sie jak inna jednostka. */
const fmtPct = (n) => `${NF1.format(n)}%`;
/** Liczba wiodaca i osie: bez sztucznego zera na koncu. */
const fmtPctShort = (n) => `${NF.format(n)}%`;
// useGrouping "always": bez tego Intl zostawia 9150 bez spacji, a obok stoi 10 710
const NF_PLN = new Intl.NumberFormat("pl-PL", { useGrouping: "always", maximumFractionDigits: 0 });
const fmtPLN = (n) => `${NF_PLN.format(Math.round(n))} zł`;
const fmtPLNk = (n) => `${NF.format(Math.round(n / 1000))} tys.`;

/** Etykiety przychodza z API agregatora — zawsze przez textContent, nigdy innerHTML. */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function svg(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const key in attrs) {
    if (attrs[key] !== undefined && attrs[key] !== null) {
      node.setAttribute(key, String(attrs[key]));
    }
  }
  return node;
}

/**
 * Prostokat z zaokraglonym koncem danych. Podstawa slupka zostaje kanciasta,
 * wiec side mowi, ktory koniec jest "koncem danych": "right", "left" albo
 * "both" (dla pasma, ktore nie wyrasta z zadnej osi — np. widelki plac).
 */
function barPath(x, y, w, h, r, side) {
  const radius = Math.max(0, Math.min(r, w / 2, h / 2));
  if (radius === 0 || w <= 0) return `M${x},${y}h${Math.max(w, 0)}v${h}h${-Math.max(w, 0)}z`;
  const arcCW = (dx, dy) => `a${radius},${radius} 0 0 1 ${dx},${dy}`;

  if (side === "right") {
    return [
      `M${x},${y}`,
      `h${w - radius}`,
      arcCW(radius, radius),
      `v${h - 2 * radius}`,
      arcCW(-radius, radius),
      `h${-(w - radius)}`,
      "z",
    ].join("");
  }
  if (side === "left") {
    return [
      `M${x + radius},${y}`,
      `h${w - radius}`,
      `v${h}`,
      `h${-(w - radius)}`,
      arcCW(-radius, -radius),
      `v${-(h - 2 * radius)}`,
      arcCW(radius, -radius),
      "z",
    ].join("");
  }
  return [
    `M${x + radius},${y}`,
    `h${w - 2 * radius}`,
    arcCW(radius, radius),
    `v${h - 2 * radius}`,
    arcCW(-radius, radius),
    `h${-(w - 2 * radius)}`,
    arcCW(-radius, -radius),
    `v${-(h - 2 * radius)}`,
    arcCW(radius, -radius),
    "z",
  ].join("");
}

/** Przyblizona szerokosc tekstu — do sprawdzenia, czy etykieta zmiesci sie w marce. */
function textWidth(str, size) {
  return String(str).length * size * 0.58;
}

/**
 * Etykieta, ktora nie miesci sie w rynnie, jest skracana — nie przycinana
 * przez overflow. Pelna nazwa zostaje w tooltipie, w aria-label i w tabeli,
 * wiec nic nie ginie.
 */
function fitLabel(str, maxPx, size) {
  const text = String(str);
  if (maxPx <= 0 || textWidth(text, size) <= maxPx) return text;
  const max = Math.max(1, Math.floor(maxPx / (size * 0.58)) - 1);
  return `${text.slice(0, max).trimEnd()}…`;
}

const SENIORITY_LABEL = {
  intern: "staż",
  junior: "junior",
  mid: "mid",
  senior: "senior",
  other: "nieokreślony",
};
const SENIORITY_ORDER = ["intern", "junior", "mid", "senior"];
const SENIORITY_COLOR = {
  intern: "var(--level-intern)",
  junior: "var(--level-junior)",
  mid: "var(--level-mid)",
  senior: "var(--level-senior)",
};

const GROUP_LABEL = {
  frontend: "frontend",
  backend: "backend",
  devops: "devops",
  data: "dane",
  qa: "QA",
  ai: "AI/ML",
};
const GROUP_ORDER = ["backend", "frontend", "devops", "data", "qa", "ai"];
const GROUP_COLOR = {
  backend: "var(--series-1)",
  frontend: "var(--series-2)",
  devops: "var(--series-3)",
  data: "var(--series-4)",
  qa: "var(--series-5)",
  ai: "var(--series-6)",
};

const MODE_LABEL = { remote: "zdalnie", hybrid: "hybrydowo", office: "z biura" };
const CONTRACT_LABEL = { b2b: "B2B", employment_contract: "umowa o pracę", internship: "staż/praktyka" };
const SPLIT_COLOR = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];

/* -------------------------------------------------------------------- tooltip */

const Tip = (() => {
  const node = document.getElementById("tip");

  function show(evt, content) {
    node.replaceChildren(content);
    node.dataset.show = "1";
    move(evt);
  }

  function move(evt) {
    const rect = node.getBoundingClientRect();
    const pad = 14;
    let x = evt.clientX + pad;
    let y = evt.clientY + pad;
    if (x + rect.width > window.innerWidth - 8) x = evt.clientX - rect.width - pad;
    if (y + rect.height > window.innerHeight - 8) y = evt.clientY - rect.height - pad;
    node.style.left = `${Math.max(8, x)}px`;
    node.style.top = `${Math.max(8, y)}px`;
  }

  function hide() {
    node.dataset.show = "0";
  }

  return { show, move, hide };
})();

/** Tresc tooltipa: wartosc wiedzie, nazwa serii jest drugorzedna. */
function tipBody(title, rows, note) {
  const frag = document.createDocumentFragment();
  frag.append(el("div", "tip-title", title));
  for (const row of rows) {
    const line = el("div", "tip-row");
    const key = el("span", "tip-key");
    if (row.color) {
      const stroke = el("span", "tip-stroke");
      stroke.style.background = row.color;
      key.append(stroke);
    }
    key.append(el("span", null, row.label));
    line.append(key, el("span", "tip-val", row.value));
    frag.append(line);
  }
  if (note) frag.append(el("div", "tip-note", note));
  return frag;
}

/**
 * Obszar trafienia wiekszy niz marka — wraz z 2px przerwa i zapasem.
 * Klawiatura dostaje to samo co kursor.
 */
function attachHit(target, build) {
  target.setAttribute("tabindex", "0");
  target.setAttribute("role", "img");
  target.addEventListener("pointerenter", (e) => Tip.show(e, build()));
  target.addEventListener("pointermove", (e) => Tip.move(e));
  target.addEventListener("pointerleave", Tip.hide);
  target.addEventListener("focus", () => {
    const box = target.getBoundingClientRect();
    Tip.show({ clientX: box.left + box.width / 2, clientY: box.bottom }, build());
  });
  target.addEventListener("blur", Tip.hide);
}

/**
 * Przerysowanie przy zmianie szerokosci kontenera — tekst ma zostac czytelny
 * w kazdej szerokosci, wiec skalujemy geometrie, nie caly SVG.
 *
 * Zwraca funkcje wymuszajaca przerysowanie: obserwator podpina sie raz,
 * a zmiana sortowania czy filtra tylko wola redraw().
 */
function responsive(container, draw) {
  let last = 0;
  const render = (force) => {
    const w = container.clientWidth;
    if (!w) return;
    if (!force && Math.abs(w - last) < 2) return;
    last = w;
    container.replaceChildren(draw(w));
  };
  render(true);
  if ("ResizeObserver" in window) new ResizeObserver(() => render(false)).observe(container);
  else window.addEventListener("resize", () => render(false));
  return () => render(true);
}

/* ------------------------------------------------------------- prymitywy osi */

function niceTicks(max, count) {
  const raw = max / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || 10 * mag;
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v);
  return ticks;
}

function drawGrid(parent, ticks, scale, top, height, fmt) {
  for (const t of ticks) {
    const x = scale(t);
    parent.append(
      svg("line", { x1: x, x2: x, y1: top, y2: top + height, stroke: "var(--gridline)", "stroke-width": 1 })
    );
    const label = svg("text", {
      x,
      y: top + height + 16,
      "text-anchor": "middle",
      fill: "var(--text-muted)",
      "font-size": 11,
      "font-variant-numeric": "tabular-nums",
    });
    label.textContent = fmt(t);
    parent.append(label);
  }
}

/* -------------------------------------------------- wykres: slupki poziome */

/**
 * rows: [{ key, label, value, color, tip: () => DocumentFragment }]
 * Slupek <=24px, zaokraglony koniec danych, wartosc przy koncu (selektywnie:
 * jedna wartosc na slupek to nie "liczba przy kazdym punkcie", tylko spec
 * slupka poziomego).
 */
function horizontalBars(width, rows, opts = {}) {
  const labelW = opts.labelWidth ?? 116;
  const rowH = opts.rowHeight ?? 30;
  const valueW = opts.valueWidth ?? 64;
  const padRight = 4;
  const top = 6;
  const axisBand = opts.axisFmt ? 26 : 0;
  const plotW = Math.max(40, width - labelW - valueW - padRight);
  const plotH = rows.length * rowH;
  const height = top + plotH + axisBand + 4;

  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "presentation" });
  const max = opts.max ?? Math.max(...rows.map((r) => r.value), 1);
  const scale = (v) => labelW + (v / max) * plotW;

  if (opts.axisFmt) {
    drawGrid(root, niceTicks(max, width < 480 ? 3 : 5), scale, top, plotH, opts.axisFmt);
  }

  rows.forEach((row, i) => {
    const y = top + i * rowH;
    const barH = Math.min(24, rowH - 10);
    const barY = y + (rowH - barH) / 2;
    const w = (row.value / max) * plotW;

    const name = svg("text", {
      x: labelW - 10,
      y: barY + barH / 2,
      "text-anchor": "end",
      "dominant-baseline": "central",
      fill: "var(--text-secondary)",
      "font-size": 13,
    });
    name.textContent = fitLabel(row.label, labelW - 12, 13);
    if (name.textContent !== row.label) {
      const full = svg("title");
      full.textContent = row.label;
      name.append(full);
    }
    root.append(name);

    root.append(
      svg("path", {
        d: barPath(labelW, barY, Math.max(w, w > 0 ? 2 : 0), barH, 4, "right"),
        fill: row.color,
      })
    );

    const value = svg("text", {
      x: labelW + w + 9,
      y: barY + barH / 2,
      "dominant-baseline": "central",
      fill: "var(--text-primary)",
      "font-size": 12.5,
      "font-variant-numeric": "tabular-nums",
    });
    value.textContent = row.valueLabel;
    root.append(value);

    const hit = svg("rect", {
      x: 0,
      y,
      width,
      height: rowH,
      fill: "transparent",
      "data-hit": "1",
    });
    hit.setAttribute("aria-label", `${row.label}: ${row.valueLabel}`);
    attachHit(hit, row.tip);
    root.append(hit);
  });

  return root;
}

/* ------------------------------------------- wykres: udzialy 100% (stos) */

/**
 * Stos part-to-whole. Miedzy segmentami 2px przerwy w kolorze powierzchni —
 * nie obwodka. Etykieta wchodzi do srodka tylko wtedy, gdy sie miesci;
 * reszte niesie legenda, tooltip i tabela.
 */
function stackedShare(width, groups, series, opts = {}) {
  const labelW = opts.labelWidth ?? 78;
  const rowH = opts.rowHeight ?? 54;
  const barH = Math.min(24, rowH - 30);
  const gap = 2;
  const top = 4;
  const plotW = Math.max(40, width - labelW - 8);
  const height = top + groups.length * rowH;

  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "presentation" });

  groups.forEach((group, gi) => {
    const total = series.reduce((sum, s) => sum + (group.values[s.key] || 0), 0);
    const y = top + gi * rowH;
    const barY = y + 18;

    const name = svg("text", {
      x: 0,
      y: y + 8,
      fill: "var(--text-secondary)",
      "font-size": 13,
      "font-weight": 560,
    });
    name.textContent = group.label;
    root.append(name);

    const totalText = svg("text", {
      x: width,
      y: y + 8,
      "text-anchor": "end",
      fill: "var(--text-muted)",
      "font-size": 12,
      "font-variant-numeric": "tabular-nums",
    });
    totalText.textContent = opts.totalFmt ? opts.totalFmt(total) : fmtInt(total);
    root.append(totalText);

    let x = 0;
    series.forEach((s, si) => {
      const value = group.values[s.key] || 0;
      if (!total) return;
      const share = (value / total) * 100;
      const raw = (value / total) * plotW;
      const isLast = si === series.length - 1;
      const w = Math.max(0, isLast ? raw : raw - gap);
      if (raw <= 0) return;

      const side = si === 0 ? "left" : isLast ? "right" : "square";
      root.append(
        svg("path", {
          d:
            side === "square"
              ? `M${x},${barY}h${w}v${barH}h${-w}z`
              : barPath(x, barY, w, barH, 4, side === "left" ? "left" : "right"),
          fill: s.color,
        })
      );

      const label = fmtPct(Math.round(share * 10) / 10);
      if (w > textWidth(label, 11.5) + 14) {
        const inner = svg("text", {
          x: x + w / 2,
          y: barY + barH / 2,
          "text-anchor": "middle",
          "dominant-baseline": "central",
          fill: s.ink || "#ffffff",
          "font-size": 11.5,
          "font-weight": 600,
          "font-variant-numeric": "tabular-nums",
        });
        inner.textContent = label;
        root.append(inner);
      }

      const hit = svg("rect", {
        x,
        y: barY - 6,
        width: Math.max(w + gap, 6),
        height: barH + 12,
        fill: "transparent",
        "data-hit": "1",
      });
      hit.setAttribute("aria-label", `${group.label}, ${s.label}: ${fmtInt(value)} (${label})`);
      attachHit(hit, () =>
        tipBody(
          group.label,
          series.map((row) => ({
            color: row.color,
            label: row.label,
            value: `${fmtInt(group.values[row.key] || 0)} · ${fmtPct(
              Math.round(((group.values[row.key] || 0) / total) * 1000) / 10
            )}`,
          })),
          opts.note
        )
      );
      root.append(hit);

      x += raw;
    });
  });

  return root;
}

function legend(series) {
  const box = el("div", "legend");
  for (const s of series) {
    const item = el("span", "legend-item");
    const key = el("span", "legend-key");
    key.style.background = s.color;
    item.append(key, el("span", null, s.label));
    box.append(item);
  }
  return box;
}

/* ---------------------------------------------------- wykres: widelki plac */

function rangeBars(width, rows) {
  const labelW = 86;
  const rowH = 58;
  const bandH = 16;
  const top = 10;
  const padRight = 16;
  const plotW = Math.max(60, width - labelW - padRight);
  const axisBand = 26;
  const height = top + rows.length * rowH + axisBand;

  const max = Math.max(...rows.map((r) => r.p75)) * 1.08;
  const scale = (v) => labelW + (v / max) * plotW;

  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "presentation" });
  drawGrid(root, niceTicks(max, width < 480 ? 3 : 5), scale, top, rows.length * rowH, fmtPLNk);

  rows.forEach((row, i) => {
    const y = top + i * rowH;
    const bandY = y + 24;
    const x1 = scale(row.p25);
    const x2 = scale(row.p75);
    const xm = scale(row.median);

    const name = svg("text", {
      x: labelW - 10,
      y: bandY + bandH / 2,
      "text-anchor": "end",
      "dominant-baseline": "central",
      fill: "var(--text-secondary)",
      "font-size": 13,
      "font-weight": 560,
    });
    name.textContent = row.label;
    root.append(name);

    root.append(
      svg("path", {
        d: barPath(x1, bandY, x2 - x1, bandH, 4, "both"),
        fill: "var(--series-1)",
        "fill-opacity": 0.22,
      })
    );

    // mediana — jedyna wartosc etykietowana wprost; p25/p75 niesie tooltip i tabela
    root.append(
      svg("rect", { x: xm - 1.5, y: bandY - 3, width: 3, height: bandH + 6, fill: "var(--series-1)", rx: 1.5 })
    );
    const medianLabel = svg("text", {
      x: xm,
      y: y + 14,
      "text-anchor": xm > labelW + plotW * 0.85 ? "end" : "middle",
      fill: "var(--text-primary)",
      "font-size": 12.5,
      "font-weight": 620,
      "font-variant-numeric": "tabular-nums",
    });
    medianLabel.textContent = fmtPLN(row.median);
    root.append(medianLabel);

    const hit = svg("rect", { x: 0, y, width, height: rowH, fill: "transparent", "data-hit": "1" });
    hit.setAttribute(
      "aria-label",
      `${row.label}: mediana ${fmtPLN(row.median)}, p25 ${fmtPLN(row.p25)}, p75 ${fmtPLN(row.p75)}`
    );
    attachHit(hit, () =>
      tipBody(
        row.label,
        [
          { label: "mediana", value: fmtPLN(row.median), color: "var(--series-1)" },
          { label: "p25", value: fmtPLN(row.p25) },
          { label: "p75", value: fmtPLN(row.p75) },
        ],
        `widełki podaje ${fmtPct(row.disclosure)} ofert · n=${fmtInt(row.n)}`
      )
    );
    root.append(hit);
  });

  return root;
}

/* ------------------------------------------------------------ tabele-blizniaki */

function buildTable(captionText, head, body) {
  const wrap = el("div", "table-wrap");
  const table = el("table");
  const caption = el("caption", null, captionText);
  const thead = el("thead");
  const hrow = el("tr");
  for (const h of head) hrow.append(el("th", null, h));
  thead.append(hrow);
  const tbody = el("tbody");
  for (const row of body) {
    const tr = el("tr");
    for (const cell of row) tr.append(el("td", null, cell));
    tbody.append(tr);
  }
  table.append(caption, thead, tbody);
  wrap.append(table);
  return wrap;
}

/** Przycisk "pokaz tabele" — kazdy wykres ma czytelnego bliznaka. */
function tableToggle(container) {
  const btn = el("button", "linkish", "Pokaż tabelę");
  btn.type = "button";
  btn.setAttribute("aria-expanded", "false");
  container.hidden = true;
  btn.addEventListener("click", () => {
    container.hidden = !container.hidden;
    btn.setAttribute("aria-expanded", String(!container.hidden));
    btn.textContent = container.hidden ? "Pokaż tabelę" : "Ukryj tabelę";
  });
  btn.setAttribute("aria-controls", container.id || "");
  return btn;
}

/* ============================================================== renderowanie */

function renderHeader(report) {
  document.getElementById("run-date").textContent = report.generated_for;
  document.getElementById("run-window").textContent = report.window === "30d" ? "30 dni" : report.window;
  document.getElementById("run-queries").textContent = `${fmtInt(report.collection.queries_ok)} zapytań, ${
    report.collection.queries_failed === 0 ? "0 błędów" : `${fmtInt(report.collection.queries_failed)} błędów`
  }`;
  document.getElementById("run-corpus").textContent = fmtInt(report.collection.jobs_in_corpus);
}

function renderHero(report) {
  const market = report.market;
  const entry = (market.by_seniority.junior || 0) + (market.by_seniority.intern || 0);

  document.getElementById("hero-value").textContent = fmtPctShort(market.share_of_market.junior);

  const tiles = document.getElementById("tiles");
  const data = [
    {
      label: "Ofert w oknie",
      value: fmtInt(market.total_offers),
      note: "wszystkie poziomy, dokładna liczba",
    },
    {
      label: "Ofert junior + staż",
      value: fmtInt(entry),
      note: `z ${fmtInt(market.total_offers)} ofert`,
    },
    {
      label: "Mediana widełek junior",
      value: fmtPLN(report.salaries.by_seniority.junior.median),
      note: `z ofert, które je podają (${fmtPct(report.salaries.by_seniority.junior.disclosure_rate)})`,
    },
    {
      label: "Ofert dla seniorów",
      value: fmtPct(market.share_of_market.senior),
      note: `${fmtInt(market.by_seniority.senior)} ofert`,
    },
  ];
  for (const t of data) {
    const tile = el("div", "tile");
    tile.append(el("div", "t-label", t.label), el("div", "t-value", t.value), el("div", "t-note", t.note));
    tiles.append(tile);
  }
}

function renderMarket(report) {
  const market = report.market;

  // Poziomy nie sumuja sie do total_offers — czesc ofert agregator zostawia
  // bez klasyfikacji. Gdyby liczyc udzialy od samej sumy poziomow, wykres
  // pokazalby seniorom 52% zamiast 48,4% z raportu. Reszta jest wiec wlasnym
  // segmentem, a nie zaokragleniem w dol.
  const classified = SENIORITY_ORDER.reduce((sum, key) => sum + (market.by_seniority[key] || 0), 0);
  const unknown = Math.max(0, market.total_offers - classified);

  const series = SENIORITY_ORDER.map((key) => ({
    key,
    label: SENIORITY_LABEL[key],
    color: SENIORITY_COLOR[key],
    ink: key === "intern" ? "#0b0b0b" : "#ffffff",
  }));
  if (unknown > 0) {
    series.push({ key: "unknown", label: "bez poziomu", color: "var(--level-unknown)", ink: "#ffffff" });
  }

  const groups = [
    {
      label: "cały rynek",
      values: { ...market.by_seniority, unknown },
    },
  ];

  const chart = document.getElementById("chart-market");
  responsive(chart, (w) =>
    stackedShare(w, groups, series, { rowHeight: 54, labelWidth: 0, note: "źródło: total_results" })
  );
  chart.after(legend(series));

  const table = document.getElementById("table-market");
  const rows = SENIORITY_ORDER.map((key) => [
    SENIORITY_LABEL[key],
    fmtInt(market.by_seniority[key] || 0),
    fmtPct(market.share_of_market[key] || 0),
  ]);
  if (unknown > 0) {
    rows.push(["bez poziomu", fmtInt(unknown), fmtPct(Math.round((unknown / market.total_offers) * 1000) / 10)]);
  }
  rows.push(["razem", fmtInt(market.total_offers), fmtPct(100)]);
  table.append(
    buildTable("Oferty według poziomu doświadczenia — okno 30 dni, liczby dokładne.", ["Poziom", "Ofert", "Udział"], rows)
  );
  document.getElementById("market-toggle").append(tableToggle(table));
}

function renderTech(report) {
  const state = {
    sort: "entry_level_offers",
    groups: new Set(GROUP_ORDER),
    minTotal: 50,
  };

  const chart = document.getElementById("chart-tech");
  const tableBox = document.getElementById("table-tech");
  const note = document.getElementById("tech-note");

  const SORTS = {
    entry_level_offers: {
      label: "Liczba ofert entry-level",
      axis: fmtInt,
      value: (r) => r.entry_level_offers,
      valueLabel: (r) => fmtInt(r.entry_level_offers),
    },
    junior_ratio: {
      label: "% ofert dla początkujących",
      axis: (v) => `${NF.format(v)}%`,
      value: (r) => r.junior_ratio,
      valueLabel: (r) => fmtPct(r.junior_ratio),
    },
    total: {
      label: "Popyt ogółem",
      axis: fmtInt,
      value: (r) => r.total,
      valueLabel: (r) => fmtInt(r.total),
    },
  };

  function visibleRows() {
    const sorter = SORTS[state.sort];
    return report.tech_demand
      .filter((r) => state.groups.has(r.group) && r.total >= state.minTotal)
      .slice()
      .sort((a, b) => sorter.value(b) - sorter.value(a));
  }

  const redraw = responsive(chart, (w) => {
    const sorter = SORTS[state.sort];
    return horizontalBars(
      w,
      visibleRows().map((r) => ({
        label: r.technology,
        value: sorter.value(r),
        valueLabel: sorter.valueLabel(r),
        color: GROUP_COLOR[r.group] || "var(--series-1)",
        tip: () =>
          tipBody(
            r.technology,
            [
              { label: "entry-level (junior + staż)", value: fmtInt(r.entry_level_offers) },
              { label: "udział entry-level", value: fmtPct(r.junior_ratio) },
              { label: "wszystkie oferty", value: fmtInt(r.total) },
            ],
            `${GROUP_LABEL[r.group] || r.group} · źródło: total_results`
          ),
      })),
      { axisFmt: sorter.axis, labelWidth: w < 520 ? 96 : 120, rowHeight: 30, valueWidth: 62 }
    );
  });

  function draw() {
    const rows = visibleRows();

    note.textContent =
      state.sort === "junior_ratio"
        ? `Odsetek liczony z pełnej liczby dopasowań. Próg ${fmtInt(
            state.minTotal
          )} ofert odcina technologie, w których kilka ogłoszeń daje pozornie świetny wynik.`
        : `Liczba ofert oznaczonych jako junior lub staż. To jest liczba drzwi, a nie ich procent.`;

    redraw();

    tableBox.replaceChildren(
      buildTable(
        "Popyt na technologie według poziomu — liczby dokładne (total_results).",
        ["Technologia", "Grupa", "Staż", "Junior", "Mid", "Senior", "Razem", "Entry", "% entry"],
        rows.map((r) => [
          r.technology,
          GROUP_LABEL[r.group] || r.group,
          fmtInt(r.levels.intern || 0),
          fmtInt(r.levels.junior || 0),
          fmtInt(r.levels.mid || 0),
          fmtInt(r.levels.senior || 0),
          fmtInt(r.total),
          fmtInt(r.entry_level_offers),
          fmtPct(r.junior_ratio),
        ])
      )
    );
  }

  // sterowanie: jeden rzad nad wykresem, ktory opisuje
  const sortBox = document.getElementById("tech-sort");
  for (const key of Object.keys(SORTS)) {
    const btn = el("button", "chip", SORTS[key].label);
    btn.type = "button";
    btn.setAttribute("aria-pressed", String(key === state.sort));
    btn.addEventListener("click", () => {
      state.sort = key;
      for (const sibling of sortBox.children) sibling.setAttribute("aria-pressed", "false");
      btn.setAttribute("aria-pressed", "true");
      draw();
    });
    sortBox.append(btn);
  }

  const groupBox = document.getElementById("tech-groups");
  for (const key of GROUP_ORDER) {
    const btn = el("button", "chip");
    btn.type = "button";
    const dot = el("span", "dot");
    dot.style.background = GROUP_COLOR[key];
    btn.append(dot, el("span", null, GROUP_LABEL[key]));
    btn.setAttribute("aria-pressed", "true");
    btn.addEventListener("click", () => {
      // kolor nalezy do grupy, nie do pozycji w rankingu — filtr nigdy nie przemalowuje reszty
      if (state.groups.has(key) && state.groups.size > 1) state.groups.delete(key);
      else state.groups.add(key);
      btn.setAttribute("aria-pressed", String(state.groups.has(key)));
      draw();
    });
    groupBox.append(btn);
  }

  const minBox = document.getElementById("tech-min");
  for (const threshold of [0, 50, 200]) {
    const btn = el("button", "chip", threshold === 0 ? "bez progu" : `≥ ${threshold}`);
    btn.type = "button";
    btn.setAttribute("aria-pressed", String(threshold === state.minTotal));
    btn.addEventListener("click", () => {
      state.minTotal = threshold;
      for (const sibling of minBox.children) sibling.setAttribute("aria-pressed", "false");
      btn.setAttribute("aria-pressed", "true");
      draw();
    });
    minBox.append(btn);
  }

  document.getElementById("tech-toggle").append(tableToggle(tableBox));
  draw();
}

function renderSalaries(report) {
  const by = report.salaries.by_seniority;
  const rows = SENIORITY_ORDER.filter((key) => by[key] && by[key].sample_size >= 20).map((key) => ({
    label: SENIORITY_LABEL[key],
    median: by[key].median,
    p25: by[key].p25,
    p75: by[key].p75,
    disclosure: by[key].disclosure_rate,
    n: by[key].sample_size,
  }));

  const chart = document.getElementById("chart-salary");
  responsive(chart, (w) => rangeBars(w, rows));

  const table = document.getElementById("table-salary");
  table.append(
    buildTable(
      "Widełki miesięczne brutto z ofert, które je ujawniają. Próba, nie cały rynek.",
      ["Poziom", "p25", "Mediana", "p75", "Ujawnia widełki", "Próba"],
      rows.map((r) => [r.label, fmtPLN(r.p25), fmtPLN(r.median), fmtPLN(r.p75), fmtPct(r.disclosure), fmtInt(r.n)])
    )
  );
  document.getElementById("salary-toggle").append(tableToggle(table));

  document.getElementById("salary-gap").textContent = `${NF.format(
    Math.round((by.senior.median / by.junior.median) * 10) / 10
  )}×`;
}

function renderLearningPaths(report) {
  const paths = report.learning_paths;
  const anchors = Object.keys(paths).filter((a) => paths[a].skills.length);
  const tabs = document.getElementById("path-tabs");
  const chart = document.getElementById("chart-path");
  const meta = document.getElementById("path-meta");
  const tableBox = document.getElementById("table-path");
  let active = anchors.includes("SQL") ? "SQL" : anchors[0];

  const redraw = responsive(chart, (w) => {
    const path = paths[active];
    return horizontalBars(
      w,
      path.skills.map((s) => ({
        label: s.skill,
        value: s.share,
        valueLabel: fmtPct(s.share),
        color: "var(--series-1)",
        tip: () =>
          tipBody(
            s.skill,
            [
              { label: `ofert z „${path.anchor}"`, value: `${fmtInt(s.count)} z ${fmtInt(path.sample_size)}` },
              { label: "udział", value: fmtPct(s.share) },
            ],
            "źródło: próba ofert (maks. 50 na zapytanie)"
          ),
      })),
      { axisFmt: (v) => `${NF.format(v)}%`, labelWidth: w < 520 ? 96 : 116, max: 100, valueWidth: 54 }
    );
  });

  function draw() {
    const path = paths[active];
    meta.textContent = `Z ${fmtInt(path.sample_size)} ofert dla początkujących, w których pada „${
      path.anchor
    }". Pokazane są technologie wymieniane w co najmniej 15% z nich.`;

    redraw();

    tableBox.replaceChildren(
      buildTable(
        `Technologie współwystępujące z „${path.anchor}" w ofertach dla początkujących (n=${fmtInt(
          path.sample_size
        )}).`,
        ["Technologia", "Ofert", "Udział"],
        path.skills.map((s) => [s.skill, fmtInt(s.count), fmtPct(s.share)])
      )
    );
  }

  for (const anchor of anchors) {
    const btn = el("button", "chip", anchor);
    btn.type = "button";
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", String(anchor === active));
    btn.addEventListener("click", () => {
      active = anchor;
      for (const sibling of tabs.children) sibling.setAttribute("aria-selected", "false");
      btn.setAttribute("aria-selected", "true");
      draw();
    });
    tabs.append(btn);
  }

  document.getElementById("path-toggle").append(tableToggle(tableBox));
  draw();
}

function renderSplit(config) {
  const { data, labels, order, chartId, tableId, toggleId, caption } = config;
  const series = order.map((key, i) => ({
    key,
    label: labels[key] || key,
    color: SPLIT_COLOR[i],
    ink: "#ffffff",
  }));
  const groups = SENIORITY_ORDER.filter((key) => data[key]).map((key) => ({
    label: SENIORITY_LABEL[key],
    values: data[key],
  }));

  const chart = document.getElementById(chartId);
  responsive(chart, (w) => stackedShare(w, groups, series, { labelWidth: 0, totalFmt: (t) => `${fmtInt(t)} ofert` }));
  chart.after(legend(series));

  const table = document.getElementById(tableId);
  table.append(
    buildTable(
      caption,
      ["Poziom", ...series.map((s) => s.label), "Razem"],
      groups.map((g) => {
        const total = series.reduce((sum, s) => sum + (g.values[s.key] || 0), 0);
        return [g.label, ...series.map((s) => fmtInt(g.values[s.key] || 0)), fmtInt(total)];
      })
    )
  );
  document.getElementById(toggleId).append(tableToggle(table));
}

function renderEmployers(report) {
  const rows = report.top_junior_employers;
  const chart = document.getElementById("chart-employers");
  responsive(chart, (w) =>
    horizontalBars(
      w,
      rows.map((r) => ({
        label: r.company,
        value: r.offers,
        valueLabel: fmtInt(r.offers),
        color: "var(--series-1)",
        tip: () =>
          tipBody(
            r.company,
            [{ label: "ofert dla początkujących w próbie", value: fmtInt(r.offers) }],
            "źródło: próba ofert, nie pełna liczba dopasowań"
          ),
      })),
      { axisFmt: fmtInt, labelWidth: w < 560 ? 130 : 190, rowHeight: 28, valueWidth: 44 }
    )
  );

  const table = document.getElementById("table-employers");
  table.append(
    buildTable(
      "Firmy z największą liczbą ofert dla początkujących w zebranej próbie.",
      ["Firma", "Ofert w próbie"],
      rows.map((r) => [r.company, fmtInt(r.offers)])
    )
  );
  document.getElementById("employers-toggle").append(tableToggle(table));
}

/* ------------------------------------------------------------------- start */

function setupTheme() {
  const btn = document.getElementById("theme-toggle");
  let stored = null;
  try {
    stored = localStorage.getItem("radar-theme");
  } catch (_) {
    /* tryb prywatny albo zablokowane dane witryny — motyw po prostu idzie za systemem */
  }
  if (stored === "dark" || stored === "light") document.documentElement.dataset.theme = stored;

  btn.addEventListener("click", () => {
    const isDark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = isDark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("radar-theme", next);
    } catch (_) {
      /* bez zapamietania — nic sie nie psuje */
    }
  });
}

async function loadReport() {
  const index = await fetch("data/reports/index.json", { cache: "no-cache" });
  if (!index.ok) throw new Error(`index.json: HTTP ${index.status}`);
  const { latest } = await index.json();
  if (!latest) throw new Error("brak raportów w data/reports/");
  const res = await fetch(`data/reports/${latest}.json`, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${latest}.json: HTTP ${res.status}`);
  return res.json();
}

async function main() {
  setupTheme();
  const status = document.getElementById("status");
  try {
    const report = await loadReport();
    renderHeader(report);
    renderHero(report);
    renderMarket(report);
    renderTech(report);
    renderSalaries(report);
    renderLearningPaths(report);
    renderSplit({
      data: report.work_modes,
      labels: MODE_LABEL,
      order: ["remote", "hybrid", "office"],
      chartId: "chart-modes",
      tableId: "table-modes",
      toggleId: "modes-toggle",
      caption: "Tryb pracy według poziomu — liczby dokładne (total_results).",
    });
    renderSplit({
      data: report.contracts,
      labels: CONTRACT_LABEL,
      order: ["employment_contract", "b2b", "internship"],
      chartId: "chart-contracts",
      tableId: "table-contracts",
      toggleId: "contracts-toggle",
      caption: "Forma zatrudnienia według poziomu — liczby dokładne (total_results).",
    });
    renderEmployers(report);
    status.remove();
    document.getElementById("dashboard").hidden = false;
  } catch (err) {
    status.className = "error";
    status.replaceChildren();
    status.append(el("p", null, `Nie udało się wczytać raportu: ${err.message}`));
    const hint = el("p");
    hint.append(
      document.createTextNode("Jeśli otwierasz plik bezpośrednio z dysku, przeglądarka blokuje odczyt JSON-a. Uruchom "),
      el("code", null, "python -m http.server"),
      document.createTextNode(" w katalogu projektu i wejdź na http://localhost:8000.")
    );
    status.append(hint);
  }
}

main();
