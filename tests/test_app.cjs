const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const app = require("../assets/app.js");

test("filterRows combines cached text search and exact filters", () => {
  let nameReads = 0;
  const first = {
    get name() {
      nameReads += 1;
      return "توريد البن المختص";
    },
    agency: "أمانة القصيم",
    region: "القصيم، الرياض",
    activity: "الأغذية",
    type: "عام",
  };
  const rows = [
    first,
    {
      name: "خدمات تقنية",
      agency: "وزارة أخرى",
      region: "مكة المكرمة",
      activity: "تقنية المعلومات",
      type: "محدود",
    },
  ];

  app.prepareSearchRows(rows);
  const readsAfterIndexing = nameReads;
  const filters = {
    q: "البن",
    region: "القصيم",
    activity: "الأغذية",
    type: "عام",
  };
  assert.deepEqual(app.filterRows(rows, filters), [first]);
  assert.deepEqual(app.filterRows(rows, filters), [first]);
  assert.equal(nameReads, readsAfterIndexing, "search blob must not be rebuilt while typing");
});

test("explorer routing round-trips dataset, search, filters, and page", () => {
  const expected = {
    datasetId: "awarded",
    q: "توريد قهوة",
    region: "القصيم",
    activity: "الأغذية",
    type: "عام",
    page: 17,
  };
  const hash = app.buildExplorerHash(expected);
  const route = app.parseRouteHash(hash);
  assert.equal(route.view, "explorer");
  assert.equal(route.tenderRef, null);
  assert.deepEqual(route.explorer, expected);
});

test("existing tender deep links remain compatible", () => {
  const route = app.parseRouteHash("#t/260639008661");
  assert.equal(route.view, "explorer");
  assert.equal(route.tenderRef, "260639008661");
  assert.equal(route.explorer, null);
});

test("completed structured gaps are not rendered as still missing", () => {
  assert.equal(app.isStillMissing({ complete: true }), false);
  assert.equal(app.isStillMissing({ complete: false }), true);
  assert.equal(app.isStillMissing(false), false);
  assert.equal(app.isStillMissing("required"), true);
});

test("computedShard follows sha256 first-byte modulo algorithm", async () => {
  const ref = "260639008661";
  const expected = String(createHash("sha256").update(ref).digest()[0] % 64).padStart(2, "0");
  assert.equal(await app.computedShard(ref, { count: 64 }), expected);
});

test("progressive awarded parts are loaded through manifest-addressed URLs", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  const dataset = {
    id: "awarded",
    file: "awarded_index.json",
    title: "المرساة",
    group: "tenders",
    indexParts: {
      count: 2,
      pathTemplate: "awarded_index_parts/{part}.json",
      algorithm: "sha256_first_byte_mod_2",
    },
  };
  const payloads = {
    "awarded_index.json": {
      meta: { indexParts: dataset.indexParts },
      count: 2,
      parts: [
        { part: "00", file: "awarded_index_parts/00.json", count: 1 },
        { part: "01", file: "awarded_index_parts/01.json", count: 1 },
      ],
    },
    "awarded_index_parts/00.json": { count: 1, records: [{ ref: "A", name: "الأول" }] },
    "awarded_index_parts/01.json": { count: 1, records: [{ ref: "B", name: "الثاني" }] },
  };
  app.state.cache = {};
  app.state.datasetPayloads = {};
  app.state.datasetLoads = {};
  app.state.byRef = new Map();
  app.state.datasetId = "open";
  app.state.manifest = {
    datasets: [dataset],
    assets: {
      "awarded_index.json": { sha256: "111111111111aaaaaaaa" },
      "awarded_index_parts/00.json": { sha256: "222222222222aaaaaaaa" },
      "awarded_index_parts/01.json": { sha256: "333333333333aaaaaaaa" },
    },
  };
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    const file = String(url).replace(/^\.\/data\//, "").split("?", 1)[0];
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify(payloads[file]),
    };
  };

  try {
    const loaded = await app.loadDatasetPayload(dataset);
    assert.equal(loaded._partsComplete, true);
    assert.deepEqual(
      loaded.records.map((row) => row.ref),
      ["A", "B"]
    );
    assert.deepEqual(
      calls.map((call) => call.url),
      [
        "./data/awarded_index.json?v=111111111111",
        "./data/awarded_index_parts/00.json?v=222222222222",
        "./data/awarded_index_parts/01.json?v=333333333333",
      ]
    );
    assert.ok(calls.every((call) => call.options.cache === "default"));
  } finally {
    global.fetch = originalFetch;
  }
});

test("dataset URLs use the manifest sha prefix and cacheable requests", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  app.state.cache = {};
  app.state.datasetPayloads = {};
  app.state.byRef = new Map();
  app.state.manifest = {
    datasets: [
      {
        id: "awarded",
        file: "awarded_index.json",
        title: "المرساة",
        detailShards: { count: 64, pathTemplate: "awarded_details/{shard}.json" },
      },
    ],
    assets: {
      "awarded_details/00.json": {
        sha256: "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
      },
    },
  };
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: false, status: 503, text: async () => "" };
  };

  try {
    await assert.rejects(
      app.loadAwardedDetail("123", { _detailShard: "00" }),
      /تعذر تحميل تفاصيل المنافسة 123/
    );
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "./data/awarded_details/00.json?v=abcdef123456");
    assert.equal(calls[0].options.cache, "default");
  } finally {
    global.fetch = originalFetch;
  }
});

test("progressive index descriptors resolve deterministic part paths", () => {
  assert.deepEqual(
    app.progressiveParts(
      { indexParts: { count: 2, pathTemplate: "awarded_index_parts/{part}.json" } },
      {}
    ),
    [
      { part: "00", file: "awarded_index_parts/00.json" },
      { part: "01", file: "awarded_index_parts/01.json" },
    ]
  );
});

test("accessibility and caching regressions stay absent from the shipped source", () => {
  const root = path.resolve(__dirname, "..");
  const source = fs.readFileSync(path.join(root, "assets/app.js"), "utf8");
  const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
  assert.doesNotMatch(source, /cache:\s*["']no-store["']/);
  assert.match(source, /<th scope="col">/);
  assert.match(html, /id="search"[^>]+aria-label=/);
  assert.match(html, /role="dialog"[^>]+tabindex="-1"/);
});
