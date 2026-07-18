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

  const TENDER_SETS = new Set([
    "open",
    "within_7",
    "within_30",
    "awarding",
    "examination",
    "cancelled",
    "unknown",
    "awarded",
    "ssr_tenders",
  ]);

  const FIELD_AR = {
    name: "الاسم",
    name_en: "الاسم بالإنجليزية (الأصل)",
    name_ar: "التعريب العربي",
    ref: "المرجع",
    num: "رقم المنافسة",
    agency: "الجهة",
    branch: "الفرع / الإدارة",
    type: "النوع",
    activity: "النشاط",
    deadline: "آخر موعد للتقديم",
    days: "الأيام المتبقية",
    hoursLeft: "الساعات المتبقية",
    submit: "تاريخ النشر / الإرسال",
    firstSeen: "أول رصد في المستودع",
    lastSeen: "آخر رصد في المستودع",
    status: "الحالة",
    tenderCategory: "تصنيف دورة الحياة",
    tenderCategoryBasis: "دليل تصنيف دورة الحياة",
    deadlineWindowHours: "الساعات حتى الإغلاق (محسوبة)",
    region: "المنطقة",
    bids: "عدد العروض",
    winAmount: "قيمة الترسية",
    url: "رابط اعتماد",
    sitemap_url: "رابط خريطة الموقع",
    wins: "الترسيات",
    count: "العدد",
    key: "المفتاح المعياري",
    display: "الاسم المعروض",
    total: "الإجمالي",
    value: "القيمة",
    award: "الترسية",
    act: "رمز النشاط",
    company: "الشركة",
    won: "حالة الفوز",
    bid: "قيمة العرض",
    award_text: "نص الترسية",
    _source: "مصدر البطاقة",
    activities: "الأنشطة",
    types: "الأنواع",
    agencies: "الجهات",
    branches: "الفروع",
    winner_companies: "شركات فائزة مرصودة",
    n: "التكرار",
    officialTenderId: "المعرّف الرسمي الرقمي",
    officialTenderIdString: "المعرّف الرسمي المشفّر",
    expectedAwardAt: "موعد الترسية المتوقع",
    awardAnnouncedAt: "إعلان الترسية",
    awardMode: "نمط الترسية",
    awardState: "حالة الترسية",
    awardCompleteness: "اكتمال الترسية",
    groups: "مجموعات الترسية",
    buyingCost: "تكلفة الشراء",
    bookletPrice: "سعر كراسة الشروط",
    condetionalBookletPrice: "سعر كراسة الشروط",
    financialFees: "الرسوم المالية",
    invitationCost: "تكلفة الدعوة",
    lastEnquiriesAt: "آخر موعد للاستفسارات",
    offersOpeningAt: "موعد فتح العروض",
    minutesLeft: "الدقائق المتبقية",
    lastAwardCheckedAt: "آخر فحص للترسية",
    nextAwardCheckAt: "الفحص القادم للترسية",
    sourceKind: "نوع مصدر السجل",
    baselineLinked: "مرتبط بخط الأساس",
    hasInvitations: "توجد دعوات",
    insideKSA: "داخل المملكة",
    tenderTypeId: "معرّف نوع المنافسة",
    activityId: "معرّف النشاط",
    statusId: "معرّف الحالة",
    winAmountHalalas: "قيمة الترسية بالهللات",
    bidHalalas: "قيمة العرض بالهللات",
    awardHalalas: "قيمة الترسية بالهللات",
    currency: "العملة",
    moneyConsistency: "تطابق القيم المالية",
    winnerAwardsHalalasSum: "مجموع ترسيات الفائزين بالهللات",
    deltaHalalas: "فرق التحقق بالهللات",
    method: "منهج التحويل المالي",
    componentDetails: "تفاصيل المكونات الرسمية",
    flags: "الأعلام الرسمية",
    lifecycleClassifiedAt: "وقت تصنيف دورة الحياة",
    deadlineParsedAt: "موعد الإغلاق المفسر",
    baselineFetchedAt: "وقت جلب خط الأساس",
    baselineImportedAt: "وقت استيراد خط الأساس",
    lastOfficialObservedAt: "آخر رصد رسمي",
    datesCheckedAt: "آخر فحص للتواريخ",
    relationsCheckedAt: "آخر فحص للعلاقات",
    awardCheckedAt: "آخر فحص للترسية",
    lastAttemptedAt: "آخر محاولة",
    lastError: "آخر خطأ",
  };

  const SOURCE_AR = {
    open: "المنافسات المفتوحة",
    within_7: "خلال 7 أيام",
    within_30: "خلال 30 يوماً",
    awarded: "المرساة",
    ssr_tenders: "عيّنة العرض الأولي",
    etimad_plus_phase0: "اعتماد بلس — خط الأساس",
    phase0_baseline: "خط الأساس المحفوظ",
    etimad_official_periodic: "اعتماد الرسمية — الجلب الدوري",
    etimad_official_visitor: "اعتماد الرسمية — واجهة الزائر",
    etimad_official_components: "اعتماد الرسمية — مكونات التفاصيل",
    official_plus_merged: "دمج الرسمية مع خط الأساس",
  };

  const META_KEY_AR = {
    phase: "المرحلة",
    updated_at: "آخر تحديث",
    commitment: "الالتزام",
    obtained: "ما تم الحصول عليه",
    still_missing: "ما زال ناقصاً",
    analysis_phase: "مرحلة التحليل",
    summary: "الملخص",
    inventory: "قائمة التدقيق",
    audited_at: "تاريخ التدقيق",
    open_tenders_complete: "المنافسات المفتوحة (مكتملة)",
    within_7: "خلال 7 أيام",
    within_30: "خلال 30 يوماً",
    awarded_yes_partial: "المرساة (جزئي)",
    activities_from_facets: "الأنشطة من التصنيفات",
    agencies_from_facets: "الجهات من التصنيفات",
    types_from_facets: "الأنواع من التصنيفات",
    facets_grand: "الإجمالي من التصنيفات",
    facets_active: "النشطة",
    facets_soon: "قريبة الإغلاق",
    api_agencies: "جهات واجهة البرمجة",
    api_companies: "شركات واجهة البرمجة",
    ssr_tenders: "منافسات العرض الأولي",
    ssr_companies: "شركات العرض الأولي",
    ssr_agencies: "جهات العرض الأولي",
    sitemap_urls_total: "إجمالي روابط الخريطة",
    sitemap_tenders: "منافسات الخريطة",
    sitemap_companies: "شركات الخريطة",
    sitemap_agencies: "جهات الخريطة",
    sitemap_activities: "أنشطة الخريطة",
  };

  function labelAr(key) {
    return FIELD_AR[key] || META_KEY_AR[key] || key;
  }

  const VIEWS = ["home", "inventory", "explorer", "missing"];

  const state = {
    manifest: null,
    view: "home",
    group: "tenders",
    datasetId: "open",
    explorerReady: false,
    q: "",
    region: "",
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
    filterRegion: document.getElementById("filterRegion"),
    filterActivity: document.getElementById("filterActivity"),
    filterType: document.getElementById("filterType"),
    setMeta: document.getElementById("setMeta"),
    gridHead: document.getElementById("gridHead"),
    gridBody: document.getElementById("gridBody"),
    cardList: document.getElementById("cardList"),
    pager: document.getElementById("pager"),
    detailRoot: document.getElementById("detailRoot"),
    detailRef: document.getElementById("detailRef"),
    detailTitle: document.getElementById("detailTitle"),
    detailTitleAr: document.getElementById("detailTitleAr"),
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

  function moneyFromHalalas(value) {
    if (value == null || value === "") return "—";
    try {
      const halalas = BigInt(String(value));
      const negative = halalas < 0n;
      const absolute = negative ? -halalas : halalas;
      const riyals = absolute / 100n;
      const remainder = absolute % 100n;
      const fraction = remainder
        ? `٫${Number(remainder).toLocaleString("ar-SA", {
            minimumIntegerDigits: 2,
            useGrouping: false,
          })}`
        : "";
      return `${negative ? "-" : ""}${riyals.toLocaleString("ar-SA")}${fraction} ر.س`;
    } catch {
      return String(value);
    }
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

  async function loadJSON(file, label) {
    if (state.cache[file]) return state.cache[file];
    el.setMeta.textContent = `جاري تحميل ${label || "البيانات"}…`;
    const res = await fetch(`${DATA}/${file}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`تعذر التحميل (${res.status})`);
    const text = await res.text();
    if (text.startsWith("version https://git-lfs.github.com/spec/v1")) {
      throw new Error(`الملف ${file} مؤشر Git LFS وليس بيانات JSON`);
    }
    let json;
    try {
      json = JSON.parse(text);
    } catch {
      throw new Error(`الملف ${file} ليس JSON صالحاً`);
    }
    state.cache[file] = json;
    return json;
  }

  function indexTenders(rows, source) {
    for (const row of rows) {
      if (!row?.ref) continue;
      const prev = state.byRef.get(String(row.ref));
      const candidate = {
        ...row,
        _source: row._source || source,
        _datasetSource: source,
      };
      if (!prev || candidate._detailComplete || (!prev._detailComplete && source === "awarded")) {
        state.byRef.set(String(row.ref), candidate);
      }
    }
  }

  function awardedDataset() {
    return (state.manifest?.datasets || []).find((dataset) => dataset.id === "awarded");
  }

  async function computedShard(ref) {
    const config = awardedDataset()?.detailShards;
    if (!config || !globalThis.crypto?.subtle) return null;
    const digest = new Uint8Array(
      await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(String(ref)))
    );
    return String(digest[0] % config.count).padStart(2, "0");
  }

  async function loadAwardedDetail(ref, hint = null) {
    const dataset = awardedDataset();
    const config = dataset?.detailShards;
    if (!dataset || !config) return hint;
    let shard = hint?._detailShard || (await computedShard(ref));
    if (shard == null) {
      const index = await loadJSON(dataset.file, dataset.title);
      const indexRow = (index.records || []).find((record) => String(record.ref) === String(ref));
      shard = indexRow?._detailShard;
      hint = hint || indexRow;
    }
    if (shard == null) return hint;
    const file = config.pathTemplate.replace("{shard}", String(shard).padStart(2, "0"));
    const payload = await loadJSON(file, `تفاصيل المرجع ${ref}`);
    for (const detail of payload.records || []) {
      const complete = { ...detail, _detailComplete: true, _datasetSource: "awarded" };
      state.byRef.set(String(detail.ref), complete);
    }
    return state.byRef.get(String(ref)) || hint;
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

  function regionTokens(raw) {
    if (raw == null || raw === "") return [];
    return String(raw)
      .split(/[،,]/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function collectRegions(rows) {
    const set = new Set();
    for (const r of rows) {
      for (const tok of regionTokens(r.region)) set.add(tok);
    }
    return [...set].sort((a, b) => String(a).localeCompare(String(b), "ar"));
  }

  function rowMatchesRegion(row, region) {
    if (!region) return true;
    const tokens = regionTokens(row.region);
    if (tokens.includes(region)) return true;
    // fallback: exact full-string match (covers odd separators)
    return String(row.region || "") === region;
  }

  function renderInventory() {
    const m = state.manifest;
    const obtained = m.obtained || {};
    const items = [
      ["مفتوحة في اللقطة", obtained.open_tenders_current_snapshot ?? obtained.open_tenders_complete],
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
          <span>${d.count != null ? `${fmt(d.count)} سجل` : "بيانات وصفية"}</span>
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
    if (state.region) out = out.filter((r) => rowMatchesRegion(r, state.region));
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
        { key: "extra", label: "المتبقي / القيمة" },
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
    const keys = sample ? Object.keys(sample).slice(0, 6) : ["value"];
    return keys.map((k) => ({ key: k, label: labelAr(k) }));
  }

  function bilingualTitleBlocks(row) {
    const en = row.name_en || (row.name_ar ? row.name : null);
    const ar = row.name_ar;
    if (ar && en) {
      return `<span class="name-en" lang="en" dir="ltr">${esc(en)}</span>
        <span class="name-ar">${esc(ar)}</span>
        <span class="meta">${esc(row.ref || "")}${row.num ? ` · رقم ${esc(row.num)}` : ""}</span>`;
    }
    return `<span class="name-ar">${esc(row.name || row.ref || "—")}</span>
      <span class="meta">${esc(row.ref || "")}${row.num ? ` · رقم ${esc(row.num)}` : ""}</span>`;
  }

  function mobileCardHTML(datasetId, cols, row) {
    const isTender = TENDER_SETS.has(datasetId) && row.ref;
    let title;
    if (isTender) title = bilingualTitleBlocks(row);
    else if (
      datasetId.startsWith("companies") ||
      datasetId.startsWith("agencies") ||
      datasetId === "activities" ||
      datasetId === "types"
    ) {
      title = `<span class="name-ar">${esc(row.name || row.act || row.ref || "—")}</span>`;
    } else {
      title = `<span class="name-ar">${esc(row.name || row.ref || row.act || "—")}</span>`;
    }

    const metaCols = cols.filter((c) => c.key !== "name");
    const meta = metaCols
      .map((c) => {
        let val = cellHTML(datasetId, c, row);
        // strip nested tender buttons from meta cells
        if (c.key === "name") return "";
        if (!val || val === "—") return "";
        return `<div class="m-field"><span class="m-label">${esc(c.label)}</span><span class="m-value">${val}</span></div>`;
      })
      .filter(Boolean)
      .join("");

    const refAttr = isTender ? ` data-ref="${esc(row.ref)}"` : "";
    const cta = isTender ? `<span class="m-cta">عرض التفاصيل</span>` : "";
    return `<article class="m-card${isTender ? " is-tender" : ""}"${refAttr}>
      <div class="m-card-main">${title}</div>
      <div class="m-card-fields">${meta}</div>
      ${cta}
    </article>`;
  }

  function bilingualNameHTML(row) {
    const en = row.name_en || (row.name_ar ? row.name : null);
    const ar = row.name_ar;
    if (ar && en) {
      return `<button type="button" class="tender-link" data-ref="${esc(row.ref)}">
          <span class="name-en" lang="en" dir="ltr">${esc(en)}</span>
          <span class="name-ar">${esc(ar)}</span>
        </button>
        <span class="meta">${esc(row.ref || "")}${row.num ? ` · رقم ${esc(row.num)}` : ""}</span>`;
    }
    return `<button type="button" class="tender-link" data-ref="${esc(row.ref)}">${esc(
      row.name || row.ref || "—"
    )}</button><span class="meta">${esc(row.ref || "")}${
      row.num ? ` · رقم ${esc(row.num)}` : ""
    }</span>`;
  }

  function tenderExtra(row) {
    if (row.winAmountHalalas != null) return moneyFromHalalas(row.winAmountHalalas);
    if (row.winAmount != null) return money(row.winAmount);
    if (row.days != null) return `${fmt(row.days)} يوم`;
    if (row.award) return esc(row.award);
    return "—";
  }

  function cellHTML(datasetId, col, row) {
    if (TENDER_SETS.has(datasetId) && col.key === "name") {
      return bilingualNameHTML(row);
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

  function renderArabicTree(obj, depth = 0) {
    if (obj == null) return "—";
    if (typeof obj !== "object") return esc(String(obj));
    if (Array.isArray(obj)) {
      if (!obj.length) return "—";
      if (typeof obj[0] !== "object") {
        return `<ul class="ar-list">${obj
          .slice(0, 80)
          .map((x) => `<li>${esc(String(x))}</li>`)
          .join("")}</ul>`;
      }
      return obj
        .slice(0, 40)
        .map((item, i) => `<div class="ar-block"><h4>عنصر ${fmt(i + 1)}</h4>${renderArabicTree(item, depth + 1)}</div>`)
        .join("");
    }
    return `<dl class="kv">${Object.entries(obj)
      .map(([k, v]) => {
        const label = labelAr(k);
        if (v && typeof v === "object") {
          return `<dt>${esc(label)}</dt><dd>${renderArabicTree(v, depth + 1)}</dd>`;
        }
        return `<dt>${esc(label)}</dt><dd>${esc(String(v))}</dd>`;
      })
      .join("")}</dl>`;
  }

  function renderMetaFile(json, datasetId) {
    if (el.filterRegion) el.filterRegion.hidden = true;
    el.filterActivity.hidden = true;
    el.filterType.hidden = true;
    if (el.cardList) el.cardList.innerHTML = "";
    if (datasetId === "taxonomy_observed") {
      const blocks = ["activities", "types", "agencies", "branches", "winner_companies"]
        .map((k) => {
          const arr = json[k] || [];
          return `<div class="detail-block"><h3>${esc(labelAr(k))} (${fmt(arr.length)})</h3>
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
      el.setMeta.textContent = "تصنيف مرصود من عيّنات العرض الأولي";
      return;
    }

    if (datasetId === "fetch_status" || datasetId === "inventory") {
      el.gridHead.innerHTML = "";
      el.gridBody.innerHTML = `<tr><td><div class="meta-view">${renderArabicTree(json)}</div></td></tr>`;
      el.pager.innerHTML = "";
      el.setMeta.textContent =
        datasetId === "fetch_status" ? "حالة الجلب بالعربية" : "تدقيق المخزون بالعربية";
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
      if (el.filterRegion) el.filterRegion.hidden = false;
      el.filterActivity.hidden = false;
      el.filterType.hidden = false;
      if (el.filterRegion) {
        fillSelect(el.filterRegion, collectRegions(rows), "كل المناطق");
      }
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
      if (el.filterRegion) el.filterRegion.hidden = true;
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
      if (el.cardList) el.cardList.innerHTML = `<p class="empty-hint">لا نتائج في هذه المجموعة.</p>`;
    } else {
      el.gridBody.innerHTML = slice
        .map((row) => {
          const refAttr = row.ref ? ` data-ref="${esc(row.ref)}"` : "";
          const clickable = TENDER_SETS.has(ds.id) && row.ref ? " is-clickable" : "";
          return `<tr class="${clickable}"${refAttr}>${cols
            .map(
              (c) =>
                `<td data-label="${esc(c.label)}">${cellHTML(ds.id, c, row)}</td>`
            )
            .join("")}</tr>`;
        })
        .join("");
      if (el.cardList) el.cardList.innerHTML = slice.map((row) => mobileCardHTML(ds.id, cols, row)).join("");
    }

    el.pager.innerHTML = `
      <button type="button" data-act="prev" ${state.page <= 1 ? "disabled" : ""}>السابق</button>
      <span>صفحة ${fmt(state.page)} / ${fmt(pages)} · ${fmt(rows.length)} سجل</span>
      <button type="button" data-act="next" ${state.page >= pages ? "disabled" : ""}>التالي</button>`;

    const partial = raw.meta?.partial || ds.partial ? " · تفريغ جزئي" : "";
    let metaLine = `${ds.title} · ${fmt(raw.count ?? rows.length)} سجل${partial}`;
    if (ds.id === "open") {
      const arCount = (raw.records || []).filter((r) => r.name_ar).length;
      metaLine += ` · ${fmt(arCount)} اسم إنجليزي مُعرَّب`;
    }
    el.setMeta.textContent = metaLine;
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
          const awardHalalas = b.awardHalalas ?? b.bidHalalas;
          const won = b.won ? `<span class="won">فائز</span>` : "";
          return `<li>
            <div><strong>${esc(company)}</strong> ${won}
              ${b.key ? `<span class="meta">${esc(b.key)}</span>` : ""}</div>
            <div>${
              awardHalalas != null
                ? moneyFromHalalas(awardHalalas)
                : typeof award === "number"
                  ? money(award)
                  : esc(award ?? "—")
            }</div>
          </li>`;
        })
      .join("")}</ul></div>`;
  }

  function provenanceBlock(provenance) {
    const sources = provenance?.sources || [];
    if (!sources.length) return "";
    return `<div class="detail-block"><h3>المصادر والحداثة (${fmt(sources.length)})</h3>
      <ul class="bid-list">${sources
        .map(
          (source) => `<li><div><strong>${esc(
            SOURCE_AR[source.id] || source.id || "مصدر غير مسمى"
          )}</strong>${source.layer ? `<span class="meta">${esc(source.layer)}</span>` : ""}</div>
            <div>${esc(dt(source.fetchedAt))}</div></li>`
        )
        .join("")}</ul></div>`;
  }

  function groupsBlock(groups) {
    if (!groups?.length) return "";
    return `<div class="detail-block"><h3>مجموعات الترسية (${fmt(groups.length)})</h3>${groups
      .map((group, index) => {
        const title = group.label || group.name || group.groupId || `المجموعة ${index + 1}`;
        return `<div class="ar-block"><h4>${esc(title)}</h4>${renderArabicTree(group)}</div>`;
      })
      .join("")}</div>`;
  }

  function moneyConsistencyBlock(consistency) {
    if (!consistency) return "";
    const statuses = {
      match: "متطابق",
      mismatch: "غير متطابق",
      unverifiable: "غير قابل للتحقق",
    };
    const pairs = [
      ["النتيجة", esc(statuses[consistency.status] || consistency.status || "—")],
      ["قيمة الترسية الدقيقة", esc(moneyFromHalalas(consistency.winAmountHalalas))],
      [
        "مجموع ترسيات الفائزين",
        esc(moneyFromHalalas(consistency.winnerAwardsHalalasSum)),
      ],
      ["الفرق", esc(moneyFromHalalas(consistency.deltaHalalas))],
      [
        "المنهج",
        esc(
          consistency.method === "decimal_str_halalas"
            ? "تحويل عشري دقيق إلى هللات"
            : consistency.method || "—"
        ),
      ],
    ];
    return `<div class="detail-block"><h3>التحقق المالي</h3>${kv(pairs)}</div>`;
  }

  function evidenceBlock(title, value) {
    if (!value || typeof value !== "object" || !Object.keys(value).length) return "";
    return `<div class="detail-block"><h3>${esc(title)}</h3>${renderArabicTree(value)}</div>`;
  }

  function openDetail(row) {
    if (!row) {
      el.setMeta.textContent = "تعذر العثور على سجل التفاصيل.";
      return;
    }
    el.detailRef.textContent = `المرجع ${row.ref || "—"}`;

    const enName = row.name_en || (row.name_ar ? row.name : null);
    const arName = row.name_ar;
    if (arName && enName) {
      el.detailTitle.innerHTML = `<span class="name-en" lang="en" dir="ltr">${esc(enName)}</span>`;
      el.detailTitleAr.hidden = false;
      el.detailTitleAr.textContent = arName;
    } else {
      el.detailTitle.textContent = row.name || row.ref || "بدون اسم";
      el.detailTitleAr.hidden = true;
      el.detailTitleAr.textContent = "";
    }

    el.detailSub.textContent = [row.agency, row.branch, row.region].filter(Boolean).join(" · ");
    el.etimadLink.href = row.url || "https://tenders.etimad.sa";

    const remaining =
      row.days != null
        ? `${fmt(row.days)} يوم` + (row.hoursLeft != null ? ` (${fmt(row.hoursLeft)} ساعة)` : "")
        : "—";

    const skip = new Set([
      "winners",
      "allBids",
      "groups",
      "awardGroups",
      "_provenance",
      "_freshness",
      "_evidence",
      "componentDetails",
      "flags",
      "_detailShard",
      "_detailComplete",
      "_datasetSource",
      "_source",
      "source",
      "winAmountHalalas",
      "currency",
      "moneyConsistency",
      "ref",
      "name",
      "name_en",
      "name_ar",
      "url",
    ]);
    const pairs = [["المرجع", esc(row.ref || "—")]];
    if (arName && enName) {
      pairs.push(["الاسم بالإنجليزية (الأصل)", `<span lang="en" dir="ltr">${esc(enName)}</span>`]);
      pairs.push(["التعريب العربي", esc(arName)]);
    } else {
      pairs.push(["الاسم", esc(row.name || "—")]);
    }

    const order = [
      "tenderCategory",
      "tenderCategoryBasis",
      "num",
      "agency",
      "branch",
      "region",
      "type",
      "activity",
      "deadline",
      "expectedAwardAt",
      "lastEnquiriesAt",
      "offersOpeningAt",
      "days",
      "hoursLeft",
      "deadlineWindowHours",
      "minutesLeft",
      "submit",
      "firstSeen",
      "lastSeen",
      "lastAwardCheckedAt",
      "nextAwardCheckAt",
      "status",
      "officialTenderId",
      "officialTenderIdString",
      "awardState",
      "awardMode",
      "awardCompleteness",
      "bids",
      "winAmount",
      "buyingCost",
      "bookletPrice",
      "condetionalBookletPrice",
      "financialFees",
      "invitationCost",
      "sourceKind",
      "baselineLinked",
      "hasInvitations",
      "insideKSA",
    ];
    const dateFields = new Set([
      "submit",
      "firstSeen",
      "lastSeen",
      "expectedAwardAt",
      "awardAnnouncedAt",
      "lastEnquiriesAt",
      "offersOpeningAt",
      "lastAwardCheckedAt",
      "nextAwardCheckAt",
    ]);
    const moneyFields = new Set([
      "winAmount",
      "buyingCost",
      "bookletPrice",
      "financialFees",
      "invitationCost",
    ]);
    const numberFields = new Set(["days", "hoursLeft", "minutesLeft", "bids"]);
    const seen = new Set();
    for (const k of order) {
      if (!(k in row) || skip.has(k)) continue;
      seen.add(k);
      const v = row[k];
      if (v == null || v === "") {
        pairs.push([labelAr(k), "—"]);
        continue;
      }
      let shown = String(v);
      if (k === "winAmount" && row.winAmountHalalas != null) {
        shown = moneyFromHalalas(row.winAmountHalalas);
      } else if (moneyFields.has(k)) shown = money(v);
      else if (dateFields.has(k)) shown = dt(v);
      else if (numberFields.has(k)) shown = fmt(v);
      else if (typeof v === "boolean") shown = v ? "نعم" : "لا";
      pairs.push([labelAr(k), esc(shown)]);
    }
    for (const [k, v] of Object.entries(row)) {
      if (skip.has(k) || seen.has(k)) continue;
      if (v == null || v === "" || Array.isArray(v) || typeof v === "object") continue;
      const shown = typeof v === "boolean" ? (v ? "نعم" : "لا") : String(v);
      pairs.push([labelAr(k), esc(shown)]);
    }
    pairs.push(["المتبقي (محسوب)", esc(remaining)]);
    pairs.push([
      "مصدر البطاقة",
      esc(SOURCE_AR[row._source] || SOURCE_AR[state.datasetId] || "المستودع"),
    ]);

    el.detailBody.innerHTML = [
      kv(pairs),
      provenanceBlock(row._provenance),
      evidenceBlock("حداثة الحقول والمكونات", row._freshness),
      evidenceBlock("أدلة RAW والتحقق", row._evidence),
      evidenceBlock("تفاصيل التواريخ والعلاقات الرسمية", row.componentDetails),
      evidenceBlock("الأعلام الرسمية", row.flags),
      moneyConsistencyBlock(row.moneyConsistency),
      groupsBlock(row.groups || row.awardGroups || []),
      bidsBlock("الفائزون", row.winners || []),
      bidsBlock("جميع العروض", row.allBids || []),
    ].join("");

    el.detailRoot.classList.add("is-open");
    el.detailRoot.setAttribute("aria-hidden", "false");
    document.body.classList.add("detail-open");
    const panel = el.detailRoot.querySelector(".detail-panel");
    if (panel) panel.scrollTop = 0;
    history.replaceState(null, "", `#t/${encodeURIComponent(row.ref || "")}`);
    el.setMeta.textContent = `تم تحميل تفاصيل المرجع ${row.ref || "—"}`;
  }

  function closeDetail() {
    el.detailRoot.classList.remove("is-open");
    el.detailRoot.setAttribute("aria-hidden", "true");
    document.body.classList.remove("detail-open");
    if (location.hash.startsWith("#t/")) {
      history.replaceState(null, "", "#explorer");
    }
    renderTable();
  }

  async function openByRef(ref) {
    if (!ref) return;
    const key = String(ref);
    let row = state.byRef.get(key);
    if (row?._detailShard && !row._detailComplete) {
      row = await loadAwardedDetail(key, row);
    }
    if (!row) {
      // The complete open set is already loaded by ensureExplorer. Try exactly
      // one deterministic award shard before any secondary sample.
      row = await loadAwardedDetail(key);
    }
    if (!row) {
      // Secondary samples are a final fallback only; derived 7/30 sets add no refs.
      for (const id of ["ssr_tenders"]) {
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
    if (row?._datasetSource === "ssr_tenders") {
      row = await loadAwardedDetail(key, row);
    }
    openDetail(row);
  }

  function showView(view, { updateHash = true } = {}) {
    if (!VIEWS.includes(view)) view = "home";
    state.view = view;
    document.querySelectorAll(".view").forEach((node) => {
      const on = node.dataset.view === view;
      node.classList.toggle("is-active", on);
      if (on) node.removeAttribute("hidden");
      else node.setAttribute("hidden", "");
    });
    document.querySelectorAll("[data-nav]").forEach((link) => {
      link.classList.toggle("is-active", link.getAttribute("data-nav") === view);
    });
    const footer = document.getElementById("siteFooter");
    if (footer) footer.hidden = view !== "home";

    if (updateHash && !location.hash.startsWith("#t/")) {
      const next = `#${view}`;
      if (location.hash !== next) history.replaceState(null, "", next);
    }
    window.scrollTo(0, 0);
  }

  async function ensureExplorer() {
    if (state.explorerReady) return;
    renderGroupTabs();
    renderDatasetTabs();
    await selectDataset(state.datasetId || "open", { navigate: false });
    state.explorerReady = true;
  }

  async function selectDataset(id, { navigate = true } = {}) {
    const ds = (state.manifest.datasets || []).find((d) => d.id === id);
    if (!ds) return;
    if (navigate) showView("explorer");
    state.datasetId = id;
    state.group = ds.group;
    state.page = 1;
    state.q = "";
    state.region = "";
    state.activity = "";
    state.type = "";
    if (el.search) el.search.value = "";
    if (el.filterRegion) el.filterRegion.value = "";
    renderGroupTabs();
    renderDatasetTabs();
    el.gridBody.innerHTML = `<tr><td>جاري التحميل…</td></tr>`;
    if (el.cardList) el.cardList.innerHTML = `<p class="empty-hint">جاري التحميل…</p>`;
    try {
      const json = await loadJSON(ds.file, ds.title);
      if (TENDER_SETS.has(id)) indexTenders(json.records || [], id);
      renderTable();
      state.explorerReady = true;
    } catch (err) {
      el.gridBody.innerHTML = `<tr><td>فشل التحميل: ${esc(err.message || err)}</td></tr>`;
      el.setMeta.textContent = String(err.message || err);
    }
  }

  async function routeFromHash() {
    const raw = (location.hash || "#home").replace(/^#/, "");
    if (raw.startsWith("t/")) {
      showView("explorer", { updateHash: false });
      await ensureExplorer();
      await openByRef(decodeURIComponent(raw.slice(2)));
      return;
    }
    const view = VIEWS.includes(raw) ? raw : "home";
    showView(view, { updateHash: false });
    if (view === "explorer") await ensureExplorer();
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
      selectDataset(btn.dataset.openSet, { navigate: true });
    });

    document.body.addEventListener("click", (e) => {
      const link = e.target.closest("a[data-nav]");
      if (!link) return;
      e.preventDefault();
      const view = link.getAttribute("data-nav");
      showView(view);
      if (view === "explorer") ensureExplorer();
    });

    window.addEventListener("hashchange", () => {
      routeFromHash();
    });

    el.search.addEventListener("input", () => {
      state.q = el.search.value;
      state.page = 1;
      renderTable();
    });
    if (el.filterRegion) {
      el.filterRegion.addEventListener("change", () => {
        state.region = el.filterRegion.value;
        state.page = 1;
        renderTable();
      });
    }
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

    const openFromRef = (e) => {
      const hit = e.target.closest("[data-ref]");
      if (!hit) return;
      e.preventDefault();
      e.stopPropagation();
      openByRef(hit.getAttribute("data-ref"));
    };
    el.gridBody.addEventListener("click", openFromRef);
    el.cardList?.addEventListener("click", openFromRef);

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
    showView("home", { updateHash: false });
    try {
      state.manifest = await loadJSON("manifest.json", "البيان");
      renderInventory();
      await routeFromHash();
    } catch (err) {
      if (el.metaLine) el.metaLine.textContent = `تعذر الإقلاع: ${err.message || err}`;
    }
  }

  boot();
})();
