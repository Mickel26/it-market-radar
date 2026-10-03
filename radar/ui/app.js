/* Klient lokalnego matchera.
 *
 * Rozmawia wylacznie z radar/server.py na 127.0.0.1. Nazwy firm, technologii
 * i tytuly ofert pochodza z agregatora, wiec wstawiamy je przez textContent,
 * nigdy przez innerHTML.
 */

"use strict";

const NF = new Intl.NumberFormat("pl-PL", { useGrouping: "always" });
const NF1 = new Intl.NumberFormat("pl-PL", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

const SENIORITY_LABEL = { intern: "staż", junior: "junior", mid: "mid", senior: "senior" };

const STATUS_LABEL = {
  saved: "zapisana",
  applied: "aplikowałem",
  interview: "rozmowa",
  offer: "oferta",
  rejected: "odmowa",
  dismissed: "nie dla mnie",
};

// Kolejnosc grup w lejku: najpierw to, co wymaga dzialania.
const FUNNEL_GROUPS = [
  ["interview", "Rozmowy"],
  ["offer", "Oferty"],
  ["applied", "Aplikowałem — czekam na odpowiedź"],
  ["saved", "Zapisane — do decyzji"],
  ["rejected", "Odmowy"],
  ["dismissed", "Nie dla mnie"],
];

/** Po ilu dniach bez odpowiedzi warto sie przypomniec albo odpuscic. */
const FOLLOW_UP_DAYS = 14;

const state = {
  skills: [],
  learning: [],
  seniority: ["intern", "junior"],
  salary_min: null,
  exclude_skills: [],
  exclude_companies: [],
  vocabulary: [],
  seniorities: ["intern", "junior", "mid", "senior"],
};

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function plural(count, one, few, many) {
  if (count === 1) return one;
  const tens = count % 100;
  const units = count % 10;
  return units >= 2 && units <= 4 && !(tens >= 12 && tens <= 14) ? few : many;
}

async function api(path, options) {
  const res = await fetch(path, options);
  let payload = null;
  try {
    payload = await res.json();
  } catch (_) {
    throw new Error(`serwer odpowiedział nieczytelnie (HTTP ${res.status})`);
  }
  if (!res.ok) throw new Error(payload?.error || `HTTP ${res.status}`);
  return payload;
}

function note(node, text, bad) {
  node.textContent = text;
  node.className = `note ${bad ? "bad" : "ok"}`;
}

/* ------------------------------------------------------------ edytor tagow */

function renderTags(container, list, key, extraClass) {
  container.replaceChildren();
  if (!list.length) {
    container.append(el("span", "hint", "— pusto —"));
    return;
  }
  list.forEach((value, index) => {
    const tag = el("span", `tag ${extraClass || ""}`.trim());
    tag.append(el("span", null, value));
    const remove = el("button", null, "×");
    remove.type = "button";
    remove.setAttribute("aria-label", `usuń ${value}`);
    remove.addEventListener("click", () => {
      list.splice(index, 1);
      renderTags(container, list, key, extraClass);
    });
    tag.append(remove);
    container.append(tag);
  });
}

function addSkill(list, container, input, extraClass) {
  const raw = input.value.trim();
  if (!raw) return;
  // Porownanie bez wielkosci liter, zeby "sql" i "SQL" nie wisialy obok siebie.
  if (!list.some((s) => s.toLowerCase() === raw.toLowerCase())) list.push(raw);
  input.value = "";
  renderTags(container, list, null, extraClass);
}

function wireAdder(inputId, buttonId, list, container, extraClass) {
  const input = $(inputId);
  const add = () => addSkill(list, container, input, extraClass);
  $(buttonId).addEventListener("click", add);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      add();
    }
  });
}

/* ------------------------------------------------------------------ profil */

function renderProfile() {
  renderTags($("skills"), state.skills, null, null);
  renderTags($("learning"), state.learning, null, "learn");

  const box = $("seniority");
  box.replaceChildren();
  for (const level of state.seniorities) {
    const btn = el("button", "chip", SENIORITY_LABEL[level] || level);
    btn.type = "button";
    btn.setAttribute("aria-pressed", String(state.seniority.includes(level)));
    btn.addEventListener("click", () => {
      const at = state.seniority.indexOf(level);
      if (at >= 0) {
        if (state.seniority.length === 1) return; // bez zadnego poziomu nic nie przejdzie
        state.seniority.splice(at, 1);
      } else {
        state.seniority.push(level);
      }
      btn.setAttribute("aria-pressed", String(state.seniority.includes(level)));
    });
    box.append(btn);
  }

  $("salary").value = state.salary_min ?? "";
  $("exclude-skills").value = state.exclude_skills.join(", ");
  $("exclude-companies").value = state.exclude_companies.join(", ");
}

function renderProfileSummary() {
  const levels = state.seniority.map((l) => SENIORITY_LABEL[l] || l).join(", ");
  const parts = [
    `${state.skills.length} ${plural(state.skills.length, "umiejętność", "umiejętności", "umiejętności")}`,
    state.learning.length ? `${state.learning.length} w nauce` : null,
    levels,
    state.salary_min ? `od ${NF.format(state.salary_min)} zł` : null,
  ].filter(Boolean);
  $("profile-summary").textContent = `Profil: ${parts.join(" · ")}`;
}

/** Profil ustawia sie raz - potem zwiniety, zeby nie przewijac go co wizyte. */
function collapseProfile(collapsed) {
  $("profile-card").hidden = collapsed;
  $("profile-summary-card").hidden = !collapsed;
  if (collapsed) renderProfileSummary();
}

function collectProfile() {
  const split = (value) =>
    value
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  const salary = $("salary").value.trim();
  return {
    skills: state.skills,
    learning: state.learning,
    seniority: state.seniority,
    work_modes: [],
    contracts: [],
    salary_min: salary ? Number(salary) : null,
    exclude_skills: split($("exclude-skills").value),
    exclude_companies: split($("exclude-companies").value),
  };
}

async function saveProfile() {
  const button = $("save");
  button.disabled = true;
  try {
    const payload = collectProfile();
    await api("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    Object.assign(state, {
      exclude_skills: payload.exclude_skills,
      exclude_companies: payload.exclude_companies,
      salary_min: payload.salary_min,
    });
    note($("save-note"), "Zapisano.", false);
    return true;
  } catch (err) {
    note($("save-note"), err.message, true);
    return false;
  } finally {
    button.disabled = false;
  }
}

/* --------------------------------------------------- wczytywanie z plikow */

function readFile(file, asText) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("nie udało się odczytać pliku"));
    reader.onload = () => resolve(reader.result);
    if (asText) reader.readAsText(file);
    else reader.readAsArrayBuffer(file);
  });
}

function toBase64(buffer) {
  // Porcjami, bo String.fromCharCode(...) na kilkunastu MB przepelnia stos.
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

async function extractFrom(file, kind) {
  const target = $("extract-note");
  note(target, "Czytam…", false);
  try {
    const body =
      kind === "cv"
        ? { kind, text: await readFile(file, true) }
        : { kind, b64: toBase64(await readFile(file, false)) };

    const { skills } = await api("/api/extract", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const before = state.skills.length;
    for (const skill of skills) {
      if (!state.skills.some((s) => s.toLowerCase() === skill.toLowerCase())) state.skills.push(skill);
    }
    const added = state.skills.length - before;
    renderTags($("skills"), state.skills, null, null);
    note(
      target,
      added
        ? `Dodano ${added} ${plural(added, "umiejętność", "umiejętności", "umiejętności")} — przejrzyj i wytnij nadmiar.`
        : "Nic nowego nie znaleziono.",
      false
    );
  } catch (err) {
    note(target, err.message, true);
  }
}

/* ----------------------------------------------------------- zbieranie */

function renderCorpus(corpus) {
  const box = $("corpus-state");
  box.replaceChildren();
  if (!corpus.exists) {
    box.append(
      el("p", "card-sub", "Nie masz jeszcze korpusu ofert na dysku. Bez niego nie ma czego dopasowywać.")
    );
    return;
  }
  box.append(
    el(
      "p",
      "card-sub",
      `Korpus z ${corpus.day}: ${NF.format(corpus.jobs)} ${plural(corpus.jobs, "oferta", "oferty", "ofert")}.`
    )
  );
}

let collectTimer = null;

function renderCollect(job) {
  const meter = $("collect-meter");
  const button = $("collect");
  const target = $("collect-note");

  if (job.state === "running") {
    meter.hidden = false;
    const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;
    meter.firstElementChild.style.width = `${pct}%`;
    button.disabled = true;
    note(target, job.total ? `Zapytanie ${job.done} z ${job.total} (${pct}%)` : "Startuję…", false);
    return true;
  }

  button.disabled = false;
  if (job.state === "done") {
    meter.firstElementChild.style.width = "100%";
    note(target, `Gotowe. ${job.message}`, false);
  } else if (job.state === "error") {
    meter.hidden = true;
    note(target, `Nie udało się: ${job.message}`, true);
  }
  return false;
}

async function pollCollect() {
  try {
    const job = await api("/api/collect");
    const running = renderCollect(job);
    if (!running) {
      clearInterval(collectTimer);
      collectTimer = null;
      const { corpus } = await api("/api/state");
      renderCorpus(corpus);
    }
  } catch (err) {
    clearInterval(collectTimer);
    collectTimer = null;
    note($("collect-note"), err.message, true);
  }
}

async function startCollect() {
  try {
    const job = await api("/api/collect", { method: "POST" });
    renderCollect(job);
    if (!collectTimer) collectTimer = setInterval(pollCollect, 1500);
  } catch (err) {
    note($("collect-note"), err.message, true);
  }
}

/* --------------------------------------------------------- dopasowania */

function chip(text, kind) {
  return el("span", `sk ${kind}`, text);
}

function renderMatches(data) {
  const bits = [
    `${NF.format(data.total)} ${plural(data.total, "dopasowanie", "dopasowania", "dopasowań")}`,
    data.new ? `${NF.format(data.new)} ${plural(data.new, "nowe", "nowe", "nowych")}` : null,
    data.hidden ? `${NF.format(data.hidden)} ukrytych, bo już coś przy nich zdecydowałeś` : null,
  ].filter(Boolean);
  $("result-meta").textContent = `${bits.join(" · ")} — korpus ${NF.format(data.corpus)} ofert ze snapshotu ${data.day}.`;

  const gapsBox = $("gaps-box");
  const gaps = $("gaps");
  gaps.replaceChildren();
  if (data.gaps.length) {
    gapsBox.hidden = false;
    for (const gap of data.gaps) {
      const item = el("span");
      item.append(document.createTextNode(gap.skill + " "), el("b", null, String(gap.count)));
      gaps.append(item);
    }
  } else {
    gapsBox.hidden = true;
  }

  const box = $("matches");
  box.replaceChildren();

  if (!data.matches.length) {
    box.append(
      el(
        "p",
        "card-sub",
        $("only-new").checked
          ? "Nie ma nowych dopasowań od poprzedniej sesji. Odznacz „tylko nowe”, żeby zobaczyć wszystkie."
          : "Nic nie pasuje. Najczęstsze przyczyny: za mało umiejętności w profilu, za ostre filtry albo stary korpus."
      )
    );
    return;
  }

  for (const item of data.matches) box.append(matchCard(item));
}

function matchCard(item) {
  const card = el("article", "match");
  card.dataset.id = item.id;

  const heading = el("h3");
  heading.append(el("span", "score", String(Math.round(item.score))));
  if (item.url) {
    const link = el("a", null, item.title);
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    heading.append(link);
  } else {
    heading.append(document.createTextNode(item.title));
  }
  if (item.new) heading.append(el("span", "badge new", "nowa"));
  if (item.status === "saved") heading.append(el("span", "badge saved", "zapisana"));
  card.append(heading);

  card.append(
    el("p", "meta", `${item.company} · ${item.seniority} · ${item.salary} · pokrycie ${NF1.format(item.coverage)}%`)
  );

  const chips = el("p", "chips");
  item.matched.forEach((s) => chips.append(chip(s, "have")));
  item.partial.forEach((s) => chips.append(chip(s, "learn")));
  item.missing.forEach((s) => chips.append(chip(s, "miss")));
  card.append(chips);

  const snapshot = {
    title: item.title,
    company: item.company,
    url: item.url,
    salary: item.salary,
    seniority: item.seniority,
  };
  const actions = el("div", "actions");
  const buttons =
    item.status === "saved"
      ? [["applied", "Aplikowałem", true], ["dismissed", "Nie dla mnie"], [null, "Odznacz"]]
      : [["saved", "Zapisz"], ["applied", "Aplikowałem", true], ["dismissed", "Nie dla mnie"]];
  for (const [status, label, primary] of buttons) {
    const btn = el("button", primary ? "act primary" : "act", label);
    btn.type = "button";
    btn.addEventListener("click", () => decide(item, status, snapshot, card));
    actions.append(btn);
  }
  card.append(actions);
  return card;
}

async function decide(item, status, snapshot, card) {
  try {
    const result = await api("/api/tracker", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: item.id, status, offer: snapshot }),
    });
    renderFunnel(result);
    if (status && status !== "saved") {
      // Decyzja zapadla - oferta znika z dopasowan od razu, bez przeliczania listy.
      card.remove();
    } else {
      card.replaceWith(matchCard({ ...item, status, new: false }));
    }
  } catch (err) {
    note($("run-note"), err.message, true);
  }
}

async function fetchMatches() {
  const button = $("run");
  button.disabled = true;
  note($("run-note"), "Liczę…", false);
  try {
    const min = Number($("min-score").value || 0);
    const onlyNew = $("only-new").checked ? "1" : "0";
    const data = await api(`/api/matches?limit=60&min_score=${encodeURIComponent(min)}&only_new=${onlyNew}`);
    renderMatches(data);
    note($("run-note"), "", false);
  } catch (err) {
    note($("run-note"), err.message, true);
  } finally {
    button.disabled = false;
  }
}

async function runMatch() {
  // Zapisujemy najpierw, zeby wynik zgadzal sie z tym, co widac na ekranie -
  // inaczej klikniecie "pokaz" po edycji profilu liczyloby stara wersje.
  if (!$("profile-card").hidden && !(await saveProfile())) {
    note($("run-note"), "Popraw profil przed liczeniem.", true);
    return;
  }
  await fetchMatches();
}

/* ---------------------------------------------------------------- lejek */

function statTile(label, value, noteText) {
  const tile = el("div", "tile");
  tile.append(el("div", "t-label", label), el("div", "t-value", value));
  if (noteText) tile.append(el("div", "t-note", noteText));
  return tile;
}

function renderFunnel(data) {
  const st = data.stats;
  const stats = $("funnel-stats");
  stats.replaceChildren(
    statTile("Aplikacje", NF.format(st.applied)),
    statTile(
      "Odpowiedzi",
      st.response_rate === null ? "—" : `${NF1.format(st.response_rate)}%`,
      st.applied ? `${NF.format(st.responded)} z ${NF.format(st.applied)}` : "jeszcze nic nie wysłano"
    ),
    statTile("Rozmowy", NF.format(st.interviews)),
    statTile(
      "Bez odpowiedzi > 2 tyg.",
      NF.format(st.stale),
      st.stale ? "czas się przypomnieć albo odpuścić" : null
    )
  );

  const box = $("funnel");
  box.replaceChildren();
  if (!data.offers.length) {
    box.append(
      el(
        "p",
        "card-sub",
        "Pusto. Przy dopasowaniach kliknij „Zapisz” albo „Aplikowałem” — oferta trafi tutaj i przestanie wracać na listę."
      )
    );
    return;
  }

  for (const [status, heading] of FUNNEL_GROUPS) {
    const items = data.offers.filter((o) => o.status === status);
    if (!items.length) continue;

    // "Nie dla mnie" zwiniete: to archiwum, nie lista do dzialania.
    const group = status === "dismissed" ? el("details", "funnel-group") : el("div", "funnel-group");
    const title = el(status === "dismissed" ? "summary" : "h3", null, `${heading} (${items.length})`);
    if (status === "dismissed") title.style.cssText = "cursor:pointer;font-size:13px;color:var(--text-muted)";
    group.append(title);
    for (const item of items) group.append(funnelItem(item));
    box.append(group);
  }
}

function funnelItem(item) {
  const row = el("div", "app");

  const main = el("div");
  const heading = el("h4");
  if (item.url) {
    const link = el("a", null, item.title || "(oferta bez tytułu)");
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    heading.append(link);
  } else {
    heading.append(document.createTextNode(item.title || "(oferta bez tytułu)"));
  }
  main.append(heading);

  const meta = el("p", "meta");
  meta.append(document.createTextNode([item.company, item.salary].filter(Boolean).join(" · ")));
  if (item.status === "applied" && item.days_since_applied !== null) {
    const days = item.days_since_applied;
    const late = days > FOLLOW_UP_DAYS;
    meta.append(document.createTextNode(" · "));
    meta.append(
      el(
        "span",
        late ? "wait late" : "wait",
        days === 0 ? "aplikacja dziś" : `${days} ${plural(days, "dzień", "dni", "dni")} od aplikacji${late ? " — przypomnij się" : ""}`
      )
    );
  }
  main.append(meta);
  row.append(main);

  const select = document.createElement("select");
  select.setAttribute("aria-label", `status: ${item.title || "oferta"}`);
  for (const [value, label] of Object.entries(STATUS_LABEL)) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    option.selected = value === item.status;
    select.append(option);
  }
  const undo = document.createElement("option");
  undo.value = "";
  undo.textContent = "— usuń z listy —";
  select.append(undo);
  select.addEventListener("change", async () => {
    try {
      const result = await api("/api/tracker", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: item.id, status: select.value || null }),
      });
      renderFunnel(result);
    } catch (err) {
      select.value = item.status;
      alert(err.message);
    }
  });
  row.append(select);

  const noteInput = document.createElement("input");
  noteInput.type = "text";
  noteInput.className = "note";
  noteInput.placeholder = "notatka, np. rozmowa we wtorek 14:00, rekruterka Anna";
  noteInput.value = item.note || "";
  noteInput.setAttribute("aria-label", `notatka: ${item.title || "oferta"}`);
  noteInput.addEventListener("change", async () => {
    try {
      await api("/api/tracker/note", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: item.id, note: noteInput.value }),
      });
      noteInput.style.borderColor = "";
    } catch (err) {
      noteInput.style.borderColor = "#d03b3b";
      noteInput.title = err.message;
    }
  });
  row.append(noteInput);
  return row;
}

/* ------------------------------------------------------------------ start */

function setupTheme() {
  let stored = null;
  try {
    stored = localStorage.getItem("radar-theme");
  } catch (_) {
    /* tryb prywatny - motyw idzie za systemem */
  }
  if (stored === "dark" || stored === "light") document.documentElement.dataset.theme = stored;

  $("theme-toggle").addEventListener("click", () => {
    const isDark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = isDark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("radar-theme", next);
    } catch (_) {
      /* bez zapamietania - nic sie nie psuje */
    }
  });
}

async function main() {
  setupTheme();
  try {
    const data = await api("/api/state");

    state.vocabulary = data.vocabulary || [];
    state.seniorities = data.seniorities || state.seniorities;
    if (data.profile) {
      state.skills = data.profile.skills || [];
      state.learning = data.profile.learning || [];
      state.seniority = data.profile.seniority?.length ? data.profile.seniority : state.seniority;
      state.salary_min = data.profile.salary_min ?? null;
      state.exclude_skills = data.profile.exclude_skills || [];
      state.exclude_companies = data.profile.exclude_companies || [];
    }

    const vocab = $("vocab");
    for (const name of state.vocabulary) {
      const option = document.createElement("option");
      option.value = name;
      vocab.append(option);
    }

    renderProfile();
    renderCorpus(data.corpus);
    if (data.collect?.state === "running") {
      renderCollect(data.collect);
      collectTimer = setInterval(pollCollect, 1500);
    }

    wireAdder("skill-input", "skill-add", state.skills, $("skills"), null);
    wireAdder("learning-input", "learning-add", state.learning, $("learning"), "learn");
    $("save").addEventListener("click", async () => {
      if (await saveProfile()) collapseProfile(true);
    });
    $("profile-edit").addEventListener("click", () => collapseProfile(false));
    $("only-new").addEventListener("change", fetchMatches);
    $("collect").addEventListener("click", startCollect);
    $("run").addEventListener("click", runMatch);
    $("cv-file").addEventListener("change", (e) => e.target.files[0] && extractFrom(e.target.files[0], "cv"));
    $("li-file").addEventListener("change", (e) => e.target.files[0] && extractFrom(e.target.files[0], "linkedin"));

    $("boot").remove();
    $("app").hidden = false;

    renderFunnel(await api("/api/tracker"));

    // Profil ustawiony i oferty na dysku - nie ma po co kazac klikac.
    if (data.profile) {
      collapseProfile(true);
      if (data.corpus.exists) fetchMatches();
    }
  } catch (err) {
    const boot = $("boot");
    boot.className = "error";
    boot.replaceChildren();
    boot.append(el("p", null, `Nie mogę się połączyć z lokalnym serwerem: ${err.message}`));
    const hint = el("p");
    hint.append(
      document.createTextNode("Ta strona działa tylko razem z nim. Uruchom w katalogu projektu: "),
      el("code", null, "python -m radar.server")
    );
    boot.append(hint);
  }
}

main();
