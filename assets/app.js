const PAGE = 30;
const DATA = "./data";

const cache = {};
const ui = {
  open: { page: 1, q: "", activity: "", type: "", sort: "deadline" },
  horizon: { page: 1, q: "", activity: "", tab: "within_7" },
  awarded: { page: 1, q: "", activity: "", type: "" },
  agencies: { page: 1, q: "" },
};

function fmt(n) {
  if (n == null || n === "" || Number.isNaN(Number(n))) return "—";
  return Number(n).toLocaleString("ar-SA");
}

function money(n) {
  if (n == null || n === "" || n === "****") return n === "****" ? "****" : "—";
  const num = Number(n);
  if (Number.isNaN(num)) return String(n);
  return num.toLocaleString("ar-SA", { maximumFractionDigits: 0 });
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function loadSet(file) {
  if (cache[file]) return cache[file];
  const res = await fetch(`${DATA}/${file}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${file}: ${res.status}`);
  const json = await res.json();
  cache[file] = Array.isArray(json) ? json : json.records || [];
  return cache[file];
}

function uniqueSorted(rows, key) {
  return [...new Set(rows.map((r) => r[key]).filter(Boolean))].sort((a, b) =>
    String(a).localeCompare(String(b), "ar")
  );
}

function fillSelect(select, values, placeholder) {
  if (!select) return;
  const cur = select.value;
  select.innerHTML =
    `<option value="">${placeholder}</option>` +
    values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
  if ([...select.options].some((o) => o.value === cur)) select.value = cur;
}

function matchQ(row, q, keys) {
  if (!q) return true;
  const needle = q.trim().toLowerCase();
  return keys.some((k) => String(row[k] ?? "").toLowerCase().includes(needle));
}

function sortOpen(rows, mode) {
  const copy = rows.slice();
  if (mode === "name") {
    copy.sort((a, b) => String(a.name || "").localeCompare(String(b.name || ""), "ar"));
  } else if (mode === "days") {
    copy.sort((a, b) => (Number(a.days) || 9999) - (Number(b.days) || 9999));
  } else {
    copy.sort((a, b) => String(a.deadline || "9999").localeCompare(String(b.deadline || "9999")));
  }
  return copy;
}

function pagerHtml(page, pages, total) {
  return `
    <button type="button" data-act="prev" ${page <= 1 ? "disabled" : ""}>السابق</button>
    <span>صفحة ${fmt(page)} / ${fmt(pages)} · ${fmt(total)}</span>
    <button type="button" data-act="next" ${page >= pages ? "disabled" : ""}>التالي</button>
  `;
}

function slicePage(rows, page) {
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const p = Math.min(Math.max(1, page), pages);
  const start = (p - 1) * PAGE;
  return { page: p, pages, total: rows.length, rows: rows.slice(start, start + PAGE) };
}

function winnerLabel(row) {
  const w = row.winners?.[0];
  if (!w) return "—";
  return w.company || "—";
}

function renderOpen() {
  const tbody = document.querySelector('[data-table="open"] tbody');
  const pager = document.querySelector('[data-pager="open"]');
  const s = ui.open;
  let rows = cache["open.json"] || [];
  rows = rows.filter(
    (r) =>
      matchQ(r, s.q, ["name", "agency", "ref", "activity", "type"]) &&
      (!s.activity || r.activity === s.activity) &&
      (!s.type || r.type === s.type)
  );
  rows = sortOpen(rows, s.sort);
  const page = slicePage(rows, s.page);
  s.page = page.page;
  tbody.innerHTML = page.rows.length
    ? page.rows
        .map(
          (r) => `<tr>
        <td><a class="name" href="${esc(r.url || "#")}" target="_blank" rel="noopener">${esc(r.name)}</a>
          <span class="meta">${esc(r.ref)}</span></td>
        <td>${esc(r.agency)}<span class="meta">${esc(r.region || "")}</span></td>
        <td>${esc(r.activity || "—")}</td>
        <td><span class="pill">${esc(r.type || "—")}</span></td>
        <td>${esc(r.deadline || "—")}</td>
        <td>${r.days != null && Number(r.days) <= 3 ? `<span class="pill warn">${fmt(r.days)}</span>` : fmt(r.days)}</td>
      </tr>`
        )
        .join("")
    : `<tr><td colspan="6">لا توجد نتائج.</td></tr>`;
  pager.innerHTML = pagerHtml(page.page, page.pages, page.total);
}

function renderHorizon() {
  const file = `${ui.horizon.tab}.json`;
  const tbody = document.querySelector('[data-table="horizon"] tbody');
  const pager = document.querySelector('[data-pager="horizon"]');
  const s = ui.horizon;
  let rows = cache[file] || [];
  rows = rows.filter(
    (r) =>
      matchQ(r, s.q, ["name", "agency", "ref", "activity"]) &&
      (!s.activity || r.activity === s.activity)
  );
  rows = sortOpen(rows, "deadline");
  const page = slicePage(rows, s.page);
  s.page = page.page;
  tbody.innerHTML = page.rows.length
    ? page.rows
        .map(
          (r) => `<tr>
        <td><a class="name" href="${esc(r.url || "#")}" target="_blank" rel="noopener">${esc(r.name)}</a>
          <span class="meta">${esc(r.ref)}</span></td>
        <td>${esc(r.agency)}</td>
        <td>${esc(r.activity || "—")}</td>
        <td>${esc(r.deadline || "—")}</td>
        <td>${fmt(r.days)}</td>
      </tr>`
        )
        .join("")
    : `<tr><td colspan="5">لا توجد نتائج.</td></tr>`;
  pager.innerHTML = pagerHtml(page.page, page.pages, page.total);
}

function renderAwarded() {
  const tbody = document.querySelector('[data-table="awarded"] tbody');
  const pager = document.querySelector('[data-pager="awarded"]');
  const s = ui.awarded;
  let rows = cache["awarded.json"] || [];
  rows = rows.filter(
    (r) =>
      matchQ(r, s.q, ["name", "agency", "ref", "activity", "type"]) &&
      (!s.activity || r.activity === s.activity) &&
      (!s.type || r.type === s.type)
  );
  const page = slicePage(rows, s.page);
  s.page = page.page;
  tbody.innerHTML = page.rows.length
    ? page.rows
        .map(
          (r) => `<tr>
        <td><a class="name" href="${esc(r.url || "#")}" target="_blank" rel="noopener">${esc(r.name)}</a>
          <span class="meta">${esc(r.ref)}</span></td>
        <td>${esc(r.agency)}</td>
        <td>${esc(r.activity || "—")}</td>
        <td>${esc(winnerLabel(r))}</td>
        <td>${money(r.winAmount)}</td>
      </tr>`
        )
        .join("")
    : `<tr><td colspan="5">لا توجد نتائج.</td></tr>`;
  pager.innerHTML = pagerHtml(page.page, page.pages, page.total);
}

function renderSimple(file, table, cols, filterFn) {
  const tbody = document.querySelector(`[data-table="${table}"] tbody`);
  let rows = cache[file] || [];
  if (filterFn) rows = filterFn(rows);
  tbody.innerHTML = rows
    .map((r, i) => {
      if (table === "agencies") {
        return `<tr><td>${fmt(i + 1)}</td><td>${esc(r.name)}</td><td>${fmt(r.count)}</td></tr>`;
      }
      if (table === "companies") {
        return `<tr><td>${esc(r.name)}</td><td>${esc(r.wins)}</td><td>${esc(r.bids)}</td></tr>`;
      }
      return `<tr>${cols.map((c) => `<td>${c === "count" ? fmt(r[c]) : esc(r[c])}</td>`).join("")}</tr>`;
    })
    .join("");
}

function renderAgencies() {
  const tbody = document.querySelector('[data-table="agencies"] tbody');
  const pager = document.querySelector('[data-pager="agencies"]');
  const s = ui.agencies;
  let rows = (cache["agencies.json"] || []).filter((r) => matchQ(r, s.q, ["name"]));
  const page = slicePage(rows, s.page);
  s.page = page.page;
  tbody.innerHTML = page.rows
    .map(
      (r, i) =>
        `<tr><td>${fmt((page.page - 1) * PAGE + i + 1)}</td><td>${esc(r.name)}</td><td>${fmt(r.count)}</td></tr>`
    )
    .join("");
  pager.innerHTML = pagerHtml(page.page, page.pages, page.total);
}

function bindToolbar(set, state, onChange) {
  const bar = document.querySelector(`.toolbar[data-set="${set}"]`);
  if (!bar) return;
  bar.querySelector(".q")?.addEventListener("input", (e) => {
    state.q = e.target.value;
    state.page = 1;
    onChange();
  });
  bar.querySelector(".activity")?.addEventListener("change", (e) => {
    state.activity = e.target.value;
    state.page = 1;
    onChange();
  });
  bar.querySelector(".type")?.addEventListener("change", (e) => {
    state.type = e.target.value;
    state.page = 1;
    onChange();
  });
  bar.querySelector(".sort")?.addEventListener("change", (e) => {
    state.sort = e.target.value;
    state.page = 1;
    onChange();
  });
}

function bindPager(name, state, render) {
  const el = document.querySelector(`[data-pager="${name}"]`);
  el?.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn || btn.disabled) return;
    if (btn.dataset.act === "prev") state.page -= 1;
    if (btn.dataset.act === "next") state.page += 1;
    render();
  });
}

function fillStats(manifest) {
  const strip = document.getElementById("statStrip");
  const sets = manifest.sets || {};
  const facets = manifest.facets || {};
  const items = [
    ["إجمالي (facet)", facets.grand],
    ["نشطة", facets.active],
    ["قريبة الإغلاق", facets.soon],
    ["مفتوحة (مجلوبة)", sets.open],
    ["خلال 7 أيام", sets.within_7],
    ["خلال 30 يوماً", sets.within_30],
    ["مرساة (جزئية)", sets.awarded],
    ["جهات", sets.agencies],
    ["أنشطة", sets.activities],
  ];
  strip.innerHTML = items
    .map(
      ([label, val]) =>
        `<div class="stat"><b>${fmt(val)}</b><span>${esc(label)}</span></div>`
    )
    .join("");

  const meta = document.getElementById("metaLine");
  meta.textContent = `لقطة: ${manifest.generated_at || "—"} · المصدر: ${
    manifest.source || "—"
  } · المرساة: ${sets.awarded_partial ? "جزئية — الجلب مستمر" : "مكتملة"}`;

  const note = document.getElementById("awardedNote");
  if (note) {
    note.textContent = sets.awarded_partial
      ? `تفريغ جزئي: ${fmt(sets.awarded)} ترسية محفوظة — الجلب يُستأنف عند إعادة فتح المعاينة.`
      : `تفريغ مكتمل: ${fmt(sets.awarded)} ترسية.`;
  }
}

async function boot() {
  const loading = (sel) => {
    const tbody = document.querySelector(`[data-table="${sel}"] tbody`);
    if (tbody) tbody.innerHTML = `<tr><td colspan="6">جاري التحميل…</td></tr>`;
  };
  ["open", "horizon", "awarded", "agencies", "activities", "types", "companies"].forEach(loading);

  try {
    const manifest = await (await fetch(`${DATA}/manifest.json`, { cache: "no-store" })).json();
    fillStats(manifest);

    const [open, w7, w30, awarded, agencies, activities, types, companies] =
      await Promise.all([
        loadSet("open.json"),
        loadSet("within_7.json"),
        loadSet("within_30.json"),
        loadSet("awarded.json"),
        loadSet("agencies.json"),
        loadSet("activities.json"),
        loadSet("types.json"),
        loadSet("companies.json"),
      ]);

    const acts = uniqueSorted(open, "activity");
    const typesOpen = uniqueSorted(open, "type");
    const actsA = uniqueSorted(awarded, "activity");
    const typesA = uniqueSorted(awarded, "type");

    fillSelect(document.querySelector('[data-set="open"] .activity'), acts, "كل الأنشطة");
    fillSelect(document.querySelector('[data-set="open"] .type'), typesOpen, "كل الأنواع");
    fillSelect(document.querySelector('[data-set="horizon"] .activity'), uniqueSorted(w7.concat(w30), "activity"), "كل الأنشطة");
    fillSelect(document.querySelector('[data-set="awarded"] .activity'), actsA, "كل الأنشطة");
    fillSelect(document.querySelector('[data-set="awarded"] .type'), typesA, "كل الأنواع");

    bindToolbar("open", ui.open, renderOpen);
    bindToolbar("horizon", ui.horizon, renderHorizon);
    bindToolbar("awarded", ui.awarded, renderAwarded);
    bindToolbar("agencies", ui.agencies, renderAgencies);
    bindPager("open", ui.open, renderOpen);
    bindPager("horizon", ui.horizon, renderHorizon);
    bindPager("awarded", ui.awarded, renderAwarded);
    bindPager("agencies", ui.agencies, renderAgencies);

    document.querySelectorAll(".tabs .tab").forEach((btn) => {
      btn.addEventListener("click", async () => {
        document.querySelectorAll(".tabs .tab").forEach((b) => b.classList.remove("on"));
        btn.classList.add("on");
        ui.horizon.tab = btn.dataset.tab;
        ui.horizon.page = 1;
        await loadSet(`${ui.horizon.tab}.json`);
        renderHorizon();
      });
    });

    renderOpen();
    renderHorizon();
    renderAwarded();
    renderAgencies();
    renderSimple("activities.json", "activities", ["name", "count"]);
    renderSimple("types.json", "types", ["name", "count"]);
    renderSimple("companies.json", "companies", ["name", "wins", "bids"]);

    void activities;
    void types;
    void companies;
    void agencies;
  } catch (err) {
    document.getElementById("metaLine").textContent = `تعذر الإقلاع: ${err.message || err}`;
  }
}

boot();
