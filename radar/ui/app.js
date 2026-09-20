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
  $("result-meta").textContent =
    `${NF.format(data.total)} ${plural(data.total, "dopasowanie", "dopasowania", "dopasowań")} ` +
    `z ${NF.format(data.corpus)} ${plural(data.corpus, "oferty", "ofert", "ofert")} ze snapshotu ${data.day}.`;

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
        "Nic nie pasuje. Najczęstsze przyczyny: za mało umiejętności w profilu, za ostre filtry albo stary korpus."
      )
    );
    return;
  }

  for (const item of data.matches) {
    const card = el("article", "match");
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
    card.append(heading);

    card.append(
      el(
        "p",
        "meta",
        `${item.company} · ${item.seniority} · ${item.salary} · pokrycie ${NF1.format(item.coverage)}%`
      )
    );

    const chips = el("p", "chips");
    item.matched.forEach((s) => chips.append(chip(s, "have")));
    item.partial.forEach((s) => chips.append(chip(s, "learn")));
    item.missing.forEach((s) => chips.append(chip(s, "miss")));
    card.append(chips);

    box.append(card);
  }
}

async function runMatch() {
  const button = $("run");
  button.disabled = true;
  note($("run-note"), "Liczę…", false);
  try {
    // Zapisujemy najpierw, zeby wynik zgadzal sie z tym, co widac na ekranie -
    // inaczej kliknięcie "pokaz" po edycji profilu liczyloby stara wersje.
    if (!(await saveProfile())) {
      note($("run-note"), "Popraw profil przed liczeniem.", true);
      return;
    }
    const min = Number($("min-score").value || 0);
    const data = await api(`/api/matches?limit=60&min_score=${encodeURIComponent(min)}`);
    renderMatches(data);
    note($("run-note"), "", false);
  } catch (err) {
    note($("run-note"), err.message, true);
  } finally {
    button.disabled = false;
  }
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
    $("save").addEventListener("click", saveProfile);
    $("collect").addEventListener("click", startCollect);
    $("run").addEventListener("click", runMatch);
    $("cv-file").addEventListener("change", (e) => e.target.files[0] && extractFrom(e.target.files[0], "cv"));
    $("li-file").addEventListener("change", (e) => e.target.files[0] && extractFrom(e.target.files[0], "linkedin"));

    $("boot").remove();
    $("app").hidden = false;
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
