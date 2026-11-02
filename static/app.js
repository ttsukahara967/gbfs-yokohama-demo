// Map UI for /api/stations and /api/nearest.

const STATUSES = {
  ok: { label: "余裕あり", color: "#2da44e" },
  few: { label: "残りわずか（2台以下）", color: "#d4a72c" },
  empty: { label: "貸出不可（0台）", color: "#cf222e" },
  full: { label: "返却不可（満車）", color: "#8250df" },
  offline: { label: "休止中", color: "#8c959f" },
};
const REFRESH_MS = 60_000;

const map = L.map("map", { preferCanvas: true }).setView([35.4560, 139.6320], 14);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

const stationLayer = L.layerGroup().addTo(map);
const nearestLayer = L.layerGroup().addTo(map);
const hidden = { operators: new Set(), statuses: new Set() };
let data = { operators: [], stations: [] };

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function operatorById(id) {
  return data.operators.find((o) => o.id === id) ?? { name: id, color: "#000" };
}

function popupHtml(s) {
  const op = operatorById(s.operator);
  const reported = s.last_reported ? new Date(s.last_reported * 1000).toLocaleTimeString("ja-JP") : "-";
  // GBFS rental_uris is a deep link to the station, not necessarily a booking page.
  const link = s.rental_url ? `<a href="${esc(s.rental_url)}" target="_blank" rel="noopener">${esc(op.name)}で開く</a>` : "";
  return `<div class="popup">
    <div class="op">${esc(op.name)}</div>
    <h3>${esc(s.name)}</h3>
    <div class="nums">${s.bikes}<small> 台貸出可</small> / ${s.docks}<small> 台返却可</small></div>
    <div>${esc(STATUSES[s.status].label)}・更新 ${reported}</div>
    ${s.address ? `<div class="op">${esc(s.address)}</div>` : ""}
    ${link}
  </div>`;
}

function renderMarkers() {
  stationLayer.clearLayers();
  for (const s of data.stations) {
    if (hidden.operators.has(s.operator) || hidden.statuses.has(s.status)) continue;
    L.circleMarker([s.lat, s.lon], {
      radius: 7,
      color: operatorById(s.operator).color,
      weight: 3,
      fillColor: STATUSES[s.status].color,
      fillOpacity: 0.9,
      // Keep marker clicks from also triggering the map's nearest search.
      bubblingMouseEvents: false,
    })
      .bindPopup(() => popupHtml(s))
      .addTo(stationLayer);
  }
}

function toggleRow({ key, set, label, color, count, error }) {
  const row = document.createElement("label");
  row.className = "row" + (error ? " error" : "");
  row.innerHTML = `<input type="checkbox" ${set.has(key) ? "" : "checked"}>
    <span class="dot" style="background:${color}"></span>
    <span>${esc(label)}</span><span class="count">${error ? "取得失敗" : count}</span>`;
  row.querySelector("input").addEventListener("change", (e) => {
    e.target.checked ? set.delete(key) : set.add(key);
    renderMarkers();
  });
  return row;
}

function renderPanel() {
  const ops = document.getElementById("operators");
  ops.replaceChildren(
    ...data.operators.map((o) =>
      toggleRow({ key: o.id, set: hidden.operators, label: o.name, color: o.color, count: o.station_count, error: !o.available })
    )
  );
  const counts = {};
  for (const s of data.stations) counts[s.status] = (counts[s.status] ?? 0) + 1;
  const sts = document.getElementById("statuses");
  sts.replaceChildren(
    ...Object.entries(STATUSES).map(([key, v]) =>
      toggleRow({ key, set: hidden.statuses, label: v.label, color: v.color, count: counts[key] ?? 0 })
    )
  );
  const credits = data.operators.map((o) => o.attribution).join(" / ");
  document.getElementById("updated").innerHTML =
    `最終取得 ${new Date().toLocaleTimeString("ja-JP")}<br>データ: ${esc(credits)}（公共交通オープンデータセンター経由）`;
}

async function refresh() {
  try {
    const res = await fetch("/api/stations");
    if (!res.ok) throw new Error(res.status);
    data = await res.json();
    renderPanel();
    renderMarkers();
  } catch (err) {
    document.getElementById("updated").textContent = `取得に失敗しました（${err.message}）`;
  }
}

map.on("click", async (e) => {
  const want = document.querySelector("input[name=want]:checked").value;
  const { lat, lng } = e.latlng;
  const res = await fetch(`/api/nearest?lat=${lat}&lon=${lng}&want=${want}`);
  const { stations } = await res.json();

  nearestLayer.clearLayers();
  L.marker([lat, lng]).addTo(nearestLayer);
  const list = document.getElementById("nearest");
  list.replaceChildren(
    ...stations.map((s) => {
      L.polyline([[lat, lng], [s.lat, s.lon]], { color: "#0969da", dashArray: "4 6", weight: 2 }).addTo(nearestLayer);
      const li = document.createElement("li");
      const n = want === "rent" ? `${s.bikes}台` : `空き${s.docks}`;
      li.innerHTML = `${esc(s.name)}<div class="meta">${esc(operatorById(s.operator).name)}・${s.distance_m}m・${n}</div>`;
      li.addEventListener("click", () => map.setView([s.lat, s.lon], 17));
      return li;
    })
  );
  if (!stations.length) list.innerHTML = "<li>見つかりませんでした</li>";
});

refresh();
setInterval(refresh, REFRESH_MS);
