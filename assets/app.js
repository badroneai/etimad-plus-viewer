const PAGE = 30;
const DATA = "./data";

const cache = {};
const byRef = new Map();

const ui = {
  open: { page: 1, q: "", activity: "", type: "", sort: "deadline" },
  horizon: { page: 1, q: "", activity: "", tab: "within_7" },
  awarded: { page: 1, q: "", activity: "", type: "", loaded: false },
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
  return `${num.toLocaleString("ar-SA", { maximumFractionDigits: 0 })} ر.س`;
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function dt(v) {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return d.toLocaleString("ar-SA", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function indexRows(rows, source) {
  for (const row of rows) {
    if (!row?.ref) continue;
    const prev = byRef.get(row.ref);
    // prefer awarded (richer) over open if both exist
    if (!prev || source === "awarded" || (source !== "open" && prev._source === "open")) {
      byRef.set(row.ref, { ...row, _source: source });
    }
  }
}

async function loadSet(file, source) {
  if (cache[file]) return cache[file];
  const res = await fetch(`${DATA}/${file}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${file}: ${res.status}`);
  const json = await res.json();
  const rows = Array.isArray(json) ? json : json.records || [];
  cache[file] = rows;
  if (source) indexRows(rows, source);
  return rows;
}

async function ensureAwarded() {
  if (ui.awarded.loaded) return cache["awarded.json"];
  const tbody = document.querySelector('[data-table="awarded"] tbody');
  if (tbody) tbody.innerHTML = `<tr><td colspan="6">جاري تحميل المرساة (ملف كبير)…</td></tr>`;
  await loadSet("awarded.json", "awarded");
  ui.awarded.loaded = true;
  return cache["awarded.json"];
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
  return w?.company || "—";
}

function nameCell(row) {
  return `<td>
    <button type="button" class="tender-link" data-ref="${esc(row.ref)}">${esc(row.name)}</button>
    <span class="meta">${esc(row.ref)}${row.num ? ` · رقم ${esc(row.num)}` : ""}</span>
  </td>`;
}

function agencyCell(row) {
  return `<td>${esc(row.agency || "—")}${
    row.branch ? `<span class="meta">${esc(row.branch)}</span>` : ""
  }</td>`;
}

function renderOpen() {
  const tbody = document.querySelector('[data-table="open"] tbody');
  const pager = document.querySelector('[data-pager="open"]');
  const s = ui.open;
  let rows = cache["open.json"] || [];
  rows = rows.filter(
    (r) =>
      matchQ(r, s.q, ["name", "agency", "ref", "activity", "type", "branch", "region", "num"]) &&
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
        ${nameCell(r)}
        ${agencyCell(r)}
        <td>${esc(r.region || "—")}</td>
        <td>${esc(r.activity || "—")}</td>
        <td><span class="pill">${esc(r.type || "—")}</span></td>
        <td>${esc(r.deadline || "—")}</td>
        <td>${
          r.days != null && Number(r.days) <= 3
            ? `<span class="pill warn">${fmt(r.days)} يوم · ${fmt(r.hoursLeft)} س</span>`
            : `${fmt(r.days)} يوم`
        }</td>
      </tr>`
        )
        .join("")
    : `<tr><td colspan="7">لا توجد نتائج.</td></tr>`;
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
      matchQ(r, s.q, ["name", "agency", "ref", "activity", "branch", "region"]) &&
      (!s.activity || r.activity === s.activity)
  );
  rows = sortOpen(rows, "deadline");
  const page = slicePage(rows, s.page);
  s.page = page.page;
  tbody.innerHTML = page.rows.length
    ? page.rows
        .map(
          (r) => `<tr>
        ${nameCell(r)}
        ${agencyCell(r)}
        <td>${esc(r.region || "—")}</td>
        <td>${esc(r.activity || "—")}</td>
        <td>${esc(r.deadline || "—")}</td>
        <td>${fmt(r.days)} يوم · ${fmt(r.hoursLeft)} س</td>
      </tr>`
        )
        .join("")
    : `<tr><td colspan="6">لا توجد نتائج.</td></tr>`;
  pager.innerHTML = pagerHtml(page.page, page.pages, page.total);
}

function renderAwarded() {
  const tbody = document.querySelector('[data-table="awarded"] tbody');
  const pager = document.querySelector('[data-pager="awarded"]');
  if (!ui.awarded.loaded) {
    tbody.innerHTML = `<tr><td colspan="6">اضغط هنا أو انتظر التحميل…</td></tr>`;
    ensureAwarded().then(() => {
      const open = cache["open.json"] || [];
      const awarded = cache["awarded.json"] || [];
      fillSelect(
        document.querySelector('[data-set="awarded"] .activity'),
        uniqueSorted(awarded, "activity"),
        "كل الأنشطة"
      );
      fillSelect(
        document.querySelector('[data-set="awarded"] .type'),
        uniqueSorted(awarded, "type"),
        "كل الأنواع"
      );
      renderAwarded();
      void open;
    });
    return;
  }
  const s = ui.awarded;
  let rows = cache["awarded.json"] || [];
  rows = rows.filter(
    (r) =>
      matchQ(r, s.q, ["name", "agency", "ref", "activity", "type", "branch"]) &&
      (!s.activity || r.activity === s.activity) &&
      (!s.type || r.type === s.type)
  );
  const page = slicePage(rows, s.page);
  s.page = page.page;
  tbody.innerHTML = page.rows.length
    ? page.rows
        .map(
          (r) => `<tr>
        ${nameCell(r)}
        ${agencyCell(r)}
        <td>${esc(r.activity || "—")}</td>
        <td>${fmt(r.bids)}</td>
        <td>${esc(winnerLabel(r))}</td>
        <td>${money(r.winAmount)}</td>
      </tr>`
        )
        .join("")
    : `<tr><td colspan="6">لا توجد نتائج.</td></tr>`;
  pager.innerHTML = pagerHtml(page.page, page.pages, page.total);
}

function renderSimple(file, table) {
  const tbody = document.querySelector(`[data-table="${table}"] tbody`);
  const rows = cache[file] || [];
  if (table === "companies") {
    tbody.innerHTML = rows
      .map(
        (r) =>
          `<tr><td>${esc(r.name)}</td><td>${esc(r.wins)}</td><td>${esc(r.bids)}</td></tr>`
      )
      .join("");
    return;
  }
  tbody.innerHTML = rows
    .map((r) => `<tr><td>${esc(r.name)}</td><td>${fmt(r.count)}</td></tr>`)
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
        `<tr><td>${fmt((page.page - 1) * PAGE + i + 1)}</td><td>${esc(r.name)}</td><td>${fmt(
          r.count
        )}</td></tr>`
    )
    .join("");
  pager.innerHTML = pagerHtml(page.page, page.pages, page.total);
}

function kv(pairs) {
  return `<dl class="kv">${pairs
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`)
    .join("")}</dl>`;
}

function bidsBlock(title, items) {
  if (!items?.length) {
    return `<div class="detail-block"><h3>${esc(title)}</h3><p class="empty-hint">لا بيانات عروض في هذه اللقطة.</p></div>`;
  }
  return `<div class="detail-block"><h3>${esc(title)} (${fmt(items.length)})</h3>
    <ul class="bid-list">${items
      .map((b) => {
        const award = b.award ?? b.bid;
        const won = b.won ? `<span class="won">فائز</span>` : "";
        return `<li>
          <div><strong>${esc(b.company || "—")}</strong> ${won}
            <span class="meta">مفتاح: ${esc(b.key || "—")}</span></div>
          <div>${money(award)}${b.bid != null && b.award != null && b.bid !== b.award ? `<span class="meta">عرض: ${money(b.bid)}</span>` : ""}</div>
        </li>`;
      })
      .join("")}</ul></div>`;
}

function openDetail(row) {
  if (!row) return;
  const root = document.getElementById("detailRoot");
  document.getElementById("detailRef").textContent = `المرجع ${row.ref || "—"}`;
  document.getElementById("detailTitle").textContent = row.name || "بدون اسم";
  document.getElementById("detailSub").textContent = [
    row.agency,
    row.branch,
    row.region,
  ]
    .filter(Boolean)
    .join(" · ");

  const etimad = document.getElementById("etimadLink");
  if (row.url) {
    etimad.href = row.url;
    etimad.removeAttribute("aria-disabled");
    etimad.classList.remove("is-disabled");
  } else {
    etimad.href = "https://tenders.etimad.sa";
  }

  const remaining =
    row.days != null
      ? `${fmt(row.days)} يوم` + (row.hoursLeft != null ? ` (${fmt(row.hoursLeft)} ساعة)` : "")
      : "—";

  const body = document.getElementById("detailBody");
  body.innerHTML = [
    kv([
      ["رقم المنافسة", esc(row.num || "—")],
      ["المرجع", esc(row.ref || "—")],
      ["الجهة", esc(row.agency || "—")],
      ["الفرع / الإدارة", esc(row.branch || "—")],
      ["المنطقة", esc(row.region || "—")],
      ["النوع", esc(row.type || "—")],
      ["النشاط", esc(row.activity || "—")],
      ["آخر موعد للتقديم", esc(row.deadline || "—")],
      ["المتبقي", esc(remaining)],
      ["تاريخ النشر / الإرسال", esc(dt(row.submit))],
      ["أول رصد في المستودع", esc(dt(row.firstSeen))],
      ["آخر رصد في المستودع", esc(dt(row.lastSeen))],
      ["الحالة (من المصدر)", esc(row.status || "غير مذكورة في اللقطة")],
      ["عدد العروض", esc(row.bids != null ? fmt(row.bids) : "—")],
      ["قيمة الترسية", esc(row.winAmount != null ? money(row.winAmount) : "—")],
      ["مصدر البطاقة", esc(row._source || "مستودع")],
    ]),
    bidsBlock("الفائزون", row.winners || []),
    bidsBlock("جميع العروض", row.allBids || []),
  ].join("");

  root.hidden = false;
  document.body.style.overflow = "hidden";
  history.replaceState(null, "", `#t/${encodeURIComponent(row.ref)}`);
  document.getElementById("copyRef").onclick = async () => {
    try {
      await navigator.clipboard.writeText(String(row.ref || ""));
      document.getElementById("copyRef").textContent = "تم النسخ";
      setTimeout(() => {
        document.getElementById("copyRef").textContent = "نسخ المرجع";
      }, 1200);
    } catch {
      /* ignore */
    }
  };
}

function closeDetail() {
  document.getElementById("detailRoot").hidden = true;
  document.body.style.overflow = "";
  if (location.hash.startsWith("#t/")) {
    history.replaceState(null, "", location.pathname + location.search);
  }
}

async function openByRef(ref) {
  if (!ref) return;
  let row = byRef.get(ref);
  if (!row) {
    await ensureAwarded();
    row = byRef.get(ref);
  }
  if (row) openDetail(row);
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
    .map(([label, val]) => `<div class="stat"><b>${fmt(val)}</b><span>${esc(label)}</span></div>`)
    .join("");

  document.getElementById("metaLine").textContent = `لقطة: ${
    manifest.generated_at || "—"
  } · حقول كاملة من المستودع · المرساة: ${
    sets.awarded_partial ? "جزئية — الجلب مستمر" : "مكتملة"
  }`;

  const note = document.getElementById("awardedNote");
  if (note) {
    note.textContent = sets.awarded_partial
      ? `تفريغ جزئي: ${fmt(sets.awarded)} ترسية — البطاقة المحلية تعرض الفائزين وجميع العروض عند توفرها.`
      : `تفريغ مكتمل: ${fmt(sets.awarded)} ترسية.`;
  }
}

function bindDetailChrome() {
  document.getElementById("detailRoot").addEventListener("click", (e) => {
    if (e.target.closest("[data-close-detail]")) closeDetail();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDetail();
  });
  document.body.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-ref]");
    if (!btn) return;
    e.preventDefault();
    openByRef(btn.dataset.ref);
  });
}

async function boot() {
  bindDetailChrome();
  ["open", "horizon", "awarded", "agencies", "activities", "types", "companies"].forEach((sel) => {
    const tbody = document.querySelector(`[data-table="${sel}"] tbody`);
    if (tbody) tbody.innerHTML = `<tr><td colspan="7">جاري التحميل…</td></tr>`;
  });

  try {
    const manifest = await (await fetch(`${DATA}/manifest.json`, { cache: "no-store" })).json();
    fillStats(manifest);

    const [open, w7, w30, agencies, activities, types, companies] = await Promise.all([
      loadSet("open.json", "open"),
      loadSet("within_7.json", "within_7"),
      loadSet("within_30.json", "within_30"),
      loadSet("agencies.json"),
      loadSet("activities.json"),
      loadSet("types.json"),
      loadSet("companies.json"),
    ]);

    fillSelect(document.querySelector('[data-set="open"] .activity'), uniqueSorted(open, "activity"), "كل الأنشطة");
    fillSelect(document.querySelector('[data-set="open"] .type'), uniqueSorted(open, "type"), "كل الأنواع");
    fillSelect(
      document.querySelector('[data-set="horizon"] .activity'),
      uniqueSorted(w7.concat(w30), "activity"),
      "كل الأنشطة"
    );

    bindToolbar("open", ui.open, renderOpen);
    bindToolbar("horizon", ui.horizon, renderHorizon);
    bindToolbar("awarded", ui.awarded, () => {
      ensureAwarded().then(renderAwarded);
    });
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
        await loadSet(`${ui.horizon.tab}.json`, ui.horizon.tab);
        renderHorizon();
      });
    });

    // lazy-load awarded when section enters view or hash asks for it
    const awardedSec = document.getElementById("awarded");
    if (awardedSec && "IntersectionObserver" in window) {
      const io = new IntersectionObserver(
        (entries) => {
          if (entries.some((en) => en.isIntersecting)) {
            ensureAwarded().then(renderAwarded);
            io.disconnect();
          }
        },
        { rootMargin: "200px" }
      );
      io.observe(awardedSec);
    }

    renderOpen();
    renderHorizon();
    renderAgencies();
    renderSimple("activities.json", "activities");
    renderSimple("types.json", "types");
    renderSimple("companies.json", "companies");
    document.querySelector('[data-table="awarded"] tbody').innerHTML =
      `<tr><td colspan="6">سيتم تحميل المرساة عند الوصول إلى هذا القسم…</td></tr>`;

    void agencies;
    void activities;
    void types;
    void companies;

    if (location.hash.startsWith("#t/")) {
      const ref = decodeURIComponent(location.hash.slice(3));
      await openByRef(ref);
    }
  } catch (err) {
    document.getElementById("metaLine").textContent = `تعذر الإقلاع: ${err.message || err}`;
  }
}

boot();
