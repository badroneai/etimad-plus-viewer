(() => {
  const DATA = "./data";
  const PAGE = 40;

  const GROUPS = [
    { id: "tenders", title: "المنافسات" },
    { id: "taxonomy", title: "التصنيفات" },
    { id: "entities", title: "الجهات والشركات" },
    { id: "sitemap", title: "خريطة الموقع" },
    { id: "meta", title: "ميتا الجلب" },
  ];

  const TENDER_SETS = new Set(["open", "within_7", "within_30", "awarded", "ssr_tenders"]);

  const state = {
    manifest: null,
    group: "tenders",
    datasetId: "open",
    q: "",
    activity: "",
    type: "",
    page: 1,
    cache: {},
    byRef: new Map(),
    currentRows: [],
  };

  const el = {
    statStrip: document.getElementById("statStrip"),
    metaLine: document.getElementById("metaLine"),
    catalog: document.getElementById("catalog"),
    missingList: document.getElementById("missingList"),
    groupTabs: document.getElementById("groupTabs"),
    datasetTabs: document.getElementById("datasetTabs"),
    search: document.getElementById("search"),
    filterActivity: document.getElementById("filterActivity"),
    filterType: document.getElementById("filterType"),
    setMeta: document.getElementById("setMeta"),
    gridHead: document.getElementById("gridHead"),
    gridBody: document.getElementById("gridBody"),
    pager: document.getElementById("pager"),
    detailRoot: document.getElementById("detailRoot"),
    detailRef: document.getElementById("detailRef"),
    detailTitle: document.getElementById("detailTitle"),
    detailSub: document.getElementById("detailSub"),
    detailBody: document.getElementById("detailBody"),
    etimadLink: document.getElementById("etimadLink"),
    copyRef: document.getElementById("copyRef"),
    detailClose: document.getElementById("detailClose"),
    detailBackdrop: document.getElementById("detailBackdrop"),
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

  async function loadJSON(file) {
    if (state.cache[file]) return state.cache[file];
    el.setMeta.textContent = `جاري تحميل ${file}…`;
    const res = await fetch(`${DATA}/${file}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`${file}: HTTP ${res.status}`);
    const json = await res.json();
    state.cache[file] = json;
    return json;
  }

  function indexTenders(rows, source) {
    for (const row of rows) {
      if (!row?.ref) continue;
      const prev = state.byRef.get(String(row.ref));
      if (!prev || source === "awarded") {
        state.byRef.set(String(row.ref), { ...row, _source: source });
      }
    }
  }

  function datasetsInGroup(group) {
    return (state.manifest?.datasets || []).filter((d) => d.group === group);
  }

  function currentDataset() {
    return (state.manifest?.datasets || []).find((d) => d.id === state.datasetId);
  }

  function fillSelect(select, values, placeholder) {
    const cur = select.value;
    select.innerHTML =
      `<option value="">${placeholder}</option>` +
      values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
    if ([...select.options].some((o) => o.value === cur)) select.value = cur;
  }

  function renderInventory() {
    const m = state.manifest;
    const obtained = m.obtained || {};
    const items = [
      ["مفتوحة", obtained.open_tenders_complete],
      ["خلال 7", obtained.within_7],
      ["خلال 30", obtained.within_30],
      ["مرساة جزئية", obtained.awarded_yes_partial],
      ["أنشطة facets", obtained.activities_from_facets],
      ["جهات facets", obtained.agencies_from_facets],
      ["أنواع", obtained.types_from_facets],
      ["شركات خريطة", obtained.sitemap_companies],
      ["جهات خريطة", obtained.sitemap_agencies],
      ["مراجع منافسات خريطة", obtained.sitemap_tenders],
      ["شركات API", obtained.api_companies],
      ["جهات API", obtained.api_agencies],
      ["facet grand", obtained.facets_grand],
    ];
    el.statStrip.innerHTML = items
      .map(([label, val]) => `<div class="stat"><b>${fmt(val)}</b><span>${esc(label)}</span></div>`)
      .join("");
    el.metaLine.textContent = `لقطة المرآة: ${m.generated_at || "—"} · ${m.note || ""}`;

    el.catalog.innerHTML = (m.datasets || [])
      .map(
        (d) => `<button type="button" class="catalog-card" data-open-set="${esc(d.id)}">
          <strong>${esc(d.title)}</strong>
          <span>${d.count != null ? fmt(d.count) : "ملف ميتا"} · ${esc(d.file)}</span>
        </button>`
      )
      .join("");

    const missing = m.still_missing || {};
    const labels = {
      awarded_yes_remainder_after_11300: "بقية المرساة بعد 11,300",
      all_unfiltered_dump: "تفريغ all غير المفلتر",
      "82_winnerfacet_json": "ملف winnerfacet",
      winnerfacet_usable_payload: "حمولة winnerfacet صالحة",
      priority_save_non_gated_bodies: "حفظ أولوية غير مقيّد",
    };
    const entries = Object.entries(missing).filter(([, v]) => v);
    el.missingList.innerHTML = entries.length
      ? entries.map(([k]) => `<li>${esc(labels[k] || k)}</li>`).join("")
      : `<li>لا يوجد نقص معلن في حالة الجلب الحالية.</li>`;
  }

  function renderGroupTabs() {
    el.groupTabs.innerHTML = GROUPS.map(
      (g) =>
        `<button type="button" class="tab ${state.group === g.id ? "on" : ""}" data-group="${g.id}">${esc(
          g.title
        )}</button>`
    ).join("");
  }

  function renderDatasetTabs() {
    const sets = datasetsInGroup(state.group);
    el.datasetTabs.innerHTML = sets
      .map(
        (d) =>
          `<button type="button" class="chip ${state.datasetId === d.id ? "on" : ""}" data-set="${d.id}">
            ${esc(d.title)}${d.count != null ? ` <em>${fmt(d.count)}</em>` : ""}
          </button>`
      )
      .join("");
  }

  function rowSearchBlob(row) {
    return Object.values(row)
      .flatMap((v) => {
        if (v == null) return [];
        if (typeof v === "object") return [JSON.stringify(v)];
        return [String(v)];
      })
      .join(" ")
      .toLowerCase();
  }

  function filterRows(rows) {
    let out = rows;
    const q = state.q.trim().toLowerCase();
    if (q) out = out.filter((r) => rowSearchBlob(r).includes(q));
    if (state.activity) out = out.filter((r) => r.activity === state.activity);
    if (state.type) out = out.filter((r) => r.type === state.type);
    return out;
  }

  function columnsFor(datasetId, sample) {
    if (TENDER_SETS.has(datasetId)) {
      return [
        { key: "name", label: "المنافسة" },
        { key: "agency", label: "الجهة / الفرع" },
        { key: "region", label: "المنطقة" },
        { key: "activity", label: "النشاط" },
        { key: "type", label: "النوع" },
        { key: "deadline", label: "الإغلاق" },
        { key: "extra", label: "إضافي" },
      ];
    }
    if (datasetId === "tender_refs_sitemap") {
      return [
        { key: "ref", label: "المرجع" },
        { key: "sitemap_url", label: "رابط الخريطة" },
      ];
    }
    if (datasetId.startsWith("companies")) {
      return [
        { key: "name", label: "الشركة" },
        { key: "wins", label: "ترسيات" },
        { key: "bids", label: "مشاركات" },
        { key: "value", label: "قيمة / أخرى" },
        { key: "sitemap_url", label: "رابط" },
      ];
    }
    if (datasetId.startsWith("agencies") || datasetId === "activities" || datasetId === "types") {
      return [
        { key: "name", label: "الاسم" },
        { key: "count", label: "العدد" },
        { key: "sitemap_url", label: "رابط" },
      ];
    }
    if (datasetId === "activities_sitemap") {
      return [
        { key: "act", label: "رمز النشاط" },
        { key: "sitemap_url", label: "رابط الخريطة" },
      ];
    }
    // generic from sample keys
    const keys = sample ? Object.keys(sample).slice(0, 6) : ["value"];
    return keys.map((k) => ({ key: k, label: k }));
  }

  function tenderExtra(row) {
    if (row.winAmount != null) return money(row.winAmount);
    if (row.days != null) return `${fmt(row.days)} يوم`;
    if (row.award) return esc(row.award);
    return "—";
  }

  function cellHTML(datasetId, col, row) {
    if (TENDER_SETS.has(datasetId) && col.key === "name") {
      return `<button type="button" class="tender-link" data-ref="${esc(row.ref)}">${esc(
        row.name || row.ref || "—"
      )}</button><span class="meta">${esc(row.ref || "")}${
        row.num ? ` · ${esc(row.num)}` : ""
      }</span>`;
    }
    if (col.key === "agency") {
      return `${esc(row.agency || "—")}${row.branch ? `<span class="meta">${esc(row.branch)}</span>` : ""}`;
    }
    if (col.key === "extra") return tenderExtra(row);
    if (col.key === "sitemap_url" || col.key === "url") {
      const u = row[col.key];
      return u
        ? `<a href="${esc(u)}" target="_blank" rel="noopener">فتح</a>`
        : "—";
    }
    if (col.key === "count" || col.key === "wins" || col.key === "bids") return fmt(row[col.key]);
    if (col.key === "value" || col.key === "total") {
      const v = row.value ?? row.total;
      return v == null ? "—" : esc(v);
    }
    const v = row[col.key];
    if (v == null || v === "") return "—";
    if (typeof v === "object") return `<code>${esc(JSON.stringify(v).slice(0, 80))}</code>`;
    return esc(v);
  }

  function renderMetaFile(json, datasetId) {
    el.filterActivity.hidden = true;
    el.filterType.hidden = true;
    if (datasetId === "taxonomy_observed") {
      const blocks = ["activities", "types", "agencies", "branches", "winner_companies"]
        .map((k) => {
          const arr = json[k] || [];
          return `<div class="detail-block"><h3>${esc(k)} (${fmt(arr.length)})</h3>
            <ul class="bid-list">${arr
              .slice(0, 100)
              .map((x) => {
                if (typeof x === "string") return `<li><div>${esc(x)}</div></li>`;
                return `<li><div>${esc(x.value || x.name || JSON.stringify(x))}</div><div>${fmt(
                  x.n || x.count
                )}</div></li>`;
              })
              .join("")}</ul></div>`;
        })
        .join("");
      el.gridHead.innerHTML = "";
      el.gridBody.innerHTML = `<tr><td colspan="1"><div class="meta-view">${blocks}</div></td></tr>`;
      el.pager.innerHTML = "";
      el.setMeta.textContent = "تصنيف مرصود من عيّنات SSR";
      return;
    }

    if (datasetId === "fetch_status" || datasetId === "inventory") {
      el.gridHead.innerHTML = "";
      el.gridBody.innerHTML = `<tr><td><pre class="meta-pre">${esc(
        JSON.stringify(json, null, 2)
      )}</pre></td></tr>`;
      el.pager.innerHTML = "";
      el.setMeta.textContent = "ملف حالة / تدقيق كما هو من المستودع";
      return;
    }
  }

  function renderTable() {
    const ds = currentDataset();
    if (!ds) return;
    const raw = state.cache[ds.file];
    if (!raw) return;

    if (ds.group === "meta" && !Array.isArray(raw.records)) {
      renderMetaFile(raw, ds.id);
      return;
    }

    let rows = Array.isArray(raw) ? raw : raw.records || [];
    if (TENDER_SETS.has(ds.id)) {
      el.filterActivity.hidden = false;
      el.filterType.hidden = false;
      fillSelect(
        el.filterActivity,
        [...new Set(rows.map((r) => r.activity).filter(Boolean))].sort((a, b) =>
          String(a).localeCompare(String(b), "ar")
        ),
        "كل الأنشطة"
      );
      fillSelect(
        el.filterType,
        [...new Set(rows.map((r) => r.type).filter(Boolean))].sort((a, b) =>
          String(a).localeCompare(String(b), "ar")
        ),
        "كل الأنواع"
      );
    } else {
      el.filterActivity.hidden = true;
      el.filterType.hidden = true;
    }

    rows = filterRows(rows);
    state.currentRows = rows;
    const pages = Math.max(1, Math.ceil(rows.length / PAGE));
    state.page = Math.min(Math.max(1, state.page), pages);
    const slice = rows.slice((state.page - 1) * PAGE, state.page * PAGE);
    const cols = columnsFor(ds.id, slice[0]);

    el.gridHead.innerHTML = `<tr>${cols.map((c) => `<th>${esc(c.label)}</th>`).join("")}</tr>`;
    if (!slice.length) {
      el.gridBody.innerHTML = `<tr><td colspan="${cols.length}">لا نتائج في هذه المجموعة.</td></tr>`;
    } else {
      el.gridBody.innerHTML = slice
        .map((row) => {
          const refAttr = row.ref ? ` data-ref="${esc(row.ref)}"` : "";
          const clickable = TENDER_SETS.has(ds.id) && row.ref ? " is-clickable" : "";
          return `<tr class="${clickable}"${refAttr}>${cols
            .map((c) => `<td>${cellHTML(ds.id, c, row)}</td>`)
            .join("")}</tr>`;
        })
        .join("");
    }

    el.pager.innerHTML = `
      <button type="button" data-act="prev" ${state.page <= 1 ? "disabled" : ""}>السابق</button>
      <span>صفحة ${fmt(state.page)} / ${fmt(pages)} · ${fmt(rows.length)} سجل</span>
      <button type="button" data-act="next" ${state.page >= pages ? "disabled" : ""}>التالي</button>`;

    const partial = raw.meta?.partial || ds.partial ? " · جزئي" : "";
    el.setMeta.textContent = `${ds.title} · الملف ${ds.file} · ${fmt(raw.count ?? rows.length)} سجل${partial}`;
  }

  function kv(pairs) {
    return `<dl class="kv">${pairs
      .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`)
      .join("")}</dl>`;
  }

  function bidsBlock(title, items) {
    if (!items?.length) {
      return `<div class="detail-block"><h3>${esc(title)}</h3><p class="empty-hint">لا بيانات في اللقطة.</p></div>`;
    }
    return `<div class="detail-block"><h3>${esc(title)} (${fmt(items.length)})</h3>
      <ul class="bid-list">${items
        .map((b) => {
          const company = b.company || b.name || "—";
          const award = b.award ?? b.bid ?? b.award_text;
          const won = b.won ? `<span class="won">فائز</span>` : "";
          return `<li>
            <div><strong>${esc(company)}</strong> ${won}
              ${b.key ? `<span class="meta">${esc(b.key)}</span>` : ""}</div>
            <div>${typeof award === "number" ? money(award) : esc(award ?? "—")}</div>
          </li>`;
        })
        .join("")}</ul></div>`;
  }

  function openDetail(row) {
    if (!row) {
      el.setMeta.textContent = "تعذر العثور على سجل التفاصيل.";
      return;
    }
    el.detailRef.textContent = `المرجع ${row.ref || "—"}`;
    el.detailTitle.textContent = row.name || row.ref || "بدون اسم";
    el.detailSub.textContent = [row.agency, row.branch, row.region].filter(Boolean).join(" · ");
    el.etimadLink.href = row.url || "https://tenders.etimad.sa";

    const remaining =
      row.days != null
        ? `${fmt(row.days)} يوم` + (row.hoursLeft != null ? ` (${fmt(row.hoursLeft)} ساعة)` : "")
        : "—";

    // show ALL scalar fields dynamically + known rich blocks
    const skip = new Set(["winners", "allBids", "_source", "name", "url"]);
    const pairs = [
      ["الاسم", esc(row.name || "—")],
      ["المرجع", esc(row.ref || "—")],
    ];
    for (const [k, v] of Object.entries(row)) {
      if (skip.has(k)) continue;
      if (v == null || v === "" || Array.isArray(v) || (typeof v === "object" && v)) continue;
      let shown = String(v);
      if (k === "winAmount") shown = money(v);
      else if (k === "submit" || k === "firstSeen" || k === "lastSeen") shown = dt(v);
      else if (k === "days" || k === "hoursLeft" || k === "bids") shown = fmt(v);
      pairs.push([k, esc(shown)]);
    }
    pairs.push(["المتبقي (محسوب)", esc(remaining)]);
    pairs.push(["مصدر البطاقة", esc(row._source || state.datasetId)]);

    el.detailBody.innerHTML = [
      kv(pairs),
      bidsBlock("الفائزون", row.winners || []),
      bidsBlock("جميع العروض", row.allBids || []),
    ].join("");

    el.detailRoot.classList.add("is-open");
    el.detailRoot.setAttribute("aria-hidden", "false");
    document.body.classList.add("detail-open");
    history.replaceState(null, "", `#t/${encodeURIComponent(row.ref || "")}`);
  }

  function closeDetail() {
    el.detailRoot.classList.remove("is-open");
    el.detailRoot.setAttribute("aria-hidden", "true");
    document.body.classList.remove("detail-open");
    if (location.hash.startsWith("#t/")) {
      history.replaceState(null, "", location.pathname + location.search);
    }
  }

  async function openByRef(ref) {
    if (!ref) return;
    const key = String(ref);
    let row = state.byRef.get(key);
    if (!row) {
      // search current filtered/unfiltered cache for tender sets
      for (const id of TENDER_SETS) {
        const ds = (state.manifest.datasets || []).find((d) => d.id === id);
        if (!ds) continue;
        if (!state.cache[ds.file]) {
          try {
            const json = await loadJSON(ds.file);
            indexTenders(json.records || [], id);
          } catch {
            /* continue */
          }
        }
        row = state.byRef.get(key);
        if (row) break;
      }
    }
    openDetail(row);
  }

  async function selectDataset(id) {
    const ds = (state.manifest.datasets || []).find((d) => d.id === id);
    if (!ds) return;
    state.datasetId = id;
    state.group = ds.group;
    state.page = 1;
    state.q = "";
    state.activity = "";
    state.type = "";
    el.search.value = "";
    renderGroupTabs();
    renderDatasetTabs();
    el.gridBody.innerHTML = `<tr><td>جاري التحميل…</td></tr>`;
    try {
      const json = await loadJSON(ds.file);
      if (TENDER_SETS.has(id)) indexTenders(json.records || [], id);
      renderTable();
      document.getElementById("explorer")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      el.gridBody.innerHTML = `<tr><td>فشل التحميل: ${esc(err.message || err)}</td></tr>`;
      el.setMeta.textContent = String(err.message || err);
    }
  }

  function bind() {
    el.groupTabs.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-group]");
      if (!btn) return;
      state.group = btn.dataset.group;
      const first = datasetsInGroup(state.group)[0];
      if (first) selectDataset(first.id);
      else {
        renderGroupTabs();
        renderDatasetTabs();
      }
    });

    el.datasetTabs.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-set]");
      if (!btn) return;
      selectDataset(btn.dataset.set);
    });

    el.catalog.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-open-set]");
      if (!btn) return;
      selectDataset(btn.dataset.openSet);
    });

    el.search.addEventListener("input", () => {
      state.q = el.search.value;
      state.page = 1;
      renderTable();
    });
    el.filterActivity.addEventListener("change", () => {
      state.activity = el.filterActivity.value;
      state.page = 1;
      renderTable();
    });
    el.filterType.addEventListener("change", () => {
      state.type = el.filterType.value;
      state.page = 1;
      renderTable();
    });

    el.pager.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-act]");
      if (!btn || btn.disabled) return;
      if (btn.dataset.act === "prev") state.page -= 1;
      if (btn.dataset.act === "next") state.page += 1;
      renderTable();
    });

    // Robust detail open: row or button
    el.gridBody.addEventListener("click", (e) => {
      const hit = e.target.closest("[data-ref]");
      if (!hit) return;
      e.preventDefault();
      e.stopPropagation();
      openByRef(hit.getAttribute("data-ref"));
    });

    el.detailClose.addEventListener("click", closeDetail);
    el.detailBackdrop.addEventListener("click", closeDetail);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeDetail();
    });

    el.copyRef.addEventListener("click", async () => {
      const text = el.detailRef.textContent.replace(/^المرجع\s*/, "");
      try {
        await navigator.clipboard.writeText(text);
        el.copyRef.textContent = "تم النسخ";
        setTimeout(() => {
          el.copyRef.textContent = "نسخ المرجع";
        }, 1200);
      } catch {
        /* ignore */
      }
    });
  }

  async function boot() {
    bind();
    try {
      state.manifest = await loadJSON("manifest.json");
      renderInventory();
      renderGroupTabs();
      renderDatasetTabs();
      await selectDataset("open");
      if (location.hash.startsWith("#t/")) {
        await openByRef(decodeURIComponent(location.hash.slice(3)));
      }
    } catch (err) {
      el.metaLine.textContent = `تعذر الإقلاع: ${err.message || err}`;
    }
  }

  boot();
})();
