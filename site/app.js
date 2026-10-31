/* Plan My Day Discover -- static SPA over the daily JSON in data/. No tracking, no API calls to third parties
   except loading images/videos the user chooses to view. */
"use strict";

const SECTIONS = [
  { id: "news", icon: "🌍", name: "News", file: "news" },
  { id: "movies", icon: "🎬", name: "Movies & TV Shows", file: "movies" },
  { id: "gaming", icon: "🎮", name: "Gaming", file: "games" },
  { id: "music", icon: "🎵", name: "Music", file: "music" },
  { id: "sports", icon: "🏆", name: "Sports", file: "sports" },
];
const RANKINGS = [["all_time", "Best of All Time"], ["recent", "Best Recent"], ["trending", "Trending"]];
const NEWS_PERIODS = [["today", "Top 3 Today"], ["week", "Top 3 This Week"]];
const SPORT_VIEWS = [["highlights", "Top Highlights"], ["results", "Major Results & Events"], ["latest", "Latest (24h)"]];
const MEDIA = [["movie", "Movies"], ["tv", "TV Shows"]];
const STALE_HOURS = 30;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

/* ---------- data ---------- */
const cache = {};
function load(file) {
  if (!cache[file]) {
    cache[file] = fetch(`data/${file}.json`, { cache: "no-cache" }).then((r) => {
      if (!r.ok) throw new Error(`${file}: HTTP ${r.status}`);
      return r.json();
    }).catch((e) => { delete cache[file]; throw e; });
  }
  return cache[file];
}

/* ---------- formatting ---------- */
const kuwait = (iso, withTime = true) => {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00Z" : iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString("en-GB", { timeZone: "Asia/Kuwait", day: "numeric", month: "short", year: "numeric",
    ...(withTime && iso.length > 10 ? { hour: "2-digit", minute: "2-digit" } : {}) });
};
const ago = (iso) => {
  const h = (Date.now() - new Date(iso)) / 36e5;
  if (!isFinite(h)) return "";
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min ago`;
  if (h < 48) return `${Math.round(h)} h ago`;
  return `${Math.round(h / 24)} days ago`;
};
const num = (n) => (n == null ? "–" : Number(n).toLocaleString("en-US"));

/* ---------- routing ---------- */
function parse() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, qs] = raw.split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  return { parts, q: new URLSearchParams(qs || "") };
}
function href(parts, q) {
  const qs = q && [...q].length ? "?" + q.toString() : "";
  return "#/" + parts.map(encodeURIComponent).join("/") + qs;
}
function go(parts, q, replace = false) {
  const h = href(parts, q);
  if (replace) history.replaceState(null, "", h); else history.pushState(null, "", h);
  route();
}

/* clicks that end a horizontal swipe on a tab row must not switch tabs */
let downX = 0, downY = 0;
document.addEventListener("pointerdown", (e) => { downX = e.clientX; downY = e.clientY; }, true);
const wasDrag = (e) => Math.abs(e.clientX - downX) > 8 || Math.abs(e.clientY - downY) > 8;

/* ---------- shell ---------- */
function renderSections(active) {
  $("sections").innerHTML = SECTIONS.map((s) =>
    `<a class="sec" href="${esc(store.get("last:" + s.id) || "#/" + s.id)}" ${s.id === active ? 'aria-current="page"' : ""}>${s.icon} ${esc(s.name)}</a>`).join("");
}
function tabsRow(el, items, selected, onPick, role = "tab") {
  el.innerHTML = items.map(([id, label]) =>
    `<button class="${role === "tab" ? "tab" : "seg"}" role="tab" aria-selected="${id === selected}" data-id="${esc(id)}">${esc(label)}</button>`).join("");
  el.onclick = (e) => {
    const b = e.target.closest("button[data-id]");
    if (!b || wasDrag(e)) return;
    onPick(b.dataset.id);
  };
  const sel = el.querySelector('[aria-selected="true"]');
  if (sel) sel.scrollIntoView({ block: "nearest", inline: "nearest" });
}
function segGroups(groups) {
  const el = $("subtabs");
  el.innerHTML = groups.map((g, gi) => `<div class="segs" role="tablist" aria-label="${esc(g.label)}" data-g="${gi}">` +
    g.items.map(([id, label]) => `<button class="seg" role="tab" aria-selected="${id === g.selected}" data-id="${esc(id)}">${esc(label)}</button>`).join("") +
    `</div>`).join("");
  el.onclick = (e) => {
    const b = e.target.closest("button[data-id]");
    if (!b || wasDrag(e)) return;
    groups[+b.closest(".segs").dataset.g].onPick(b.dataset.id);
  };
}
function chips(items, selected, onToggle) {
  const el = $("filters");
  el.innerHTML = items.map(([id, label]) =>
    `<button class="chip" aria-pressed="${selected.includes(id)}" data-id="${esc(id)}">${esc(label)}</button>`).join("");
  el.onclick = (e) => {
    const b = e.target.closest("button[data-id]");
    if (!b || wasDrag(e)) return;
    onToggle(b.dataset.id);
  };
}
function note(text) { const n = $("note"); n.hidden = !text; n.textContent = text || ""; }
function skeleton(n = 6) { $("content").innerHTML = Array.from({ length: n }, () => '<div class="skeleton"></div>').join(""); }
function empty(text) { $("content").innerHTML = `<p class="empty">${esc(text)}</p>`; }

let meta = null;
async function showMeta(section) {
  try { meta = meta || await load("meta"); } catch { meta = { sections: {} }; }
  const file = SECTIONS.find((s) => s.id === section)?.file;
  const m = meta.sections?.[file];
  const u = $("updated");
  u.textContent = m?.updated_at ? `Updated ${kuwait(m.updated_at)} (Kuwait)` : "";
  const st = $("stale");
  const old = m?.updated_at && (Date.now() - new Date(m.updated_at)) / 36e5 > STALE_HOURS;
  if (m?.updated_at && (m.ok === false || old)) {
    st.hidden = false;
    st.textContent = `This section shows data from ${kuwait(m.updated_at)} (${ago(m.updated_at)}). The latest refresh ` +
      (m.ok === false ? "could not get new data; it will retry at the next daily update." : "is late.");
  } else st.hidden = true;
}

/* ---------- cards ---------- */
let currentItems = [];   // items on screen, for the detail sheet and filtering
let renderCard = null;
let renderDetail = null;

function paint(items, cardFn, detailFn, emptyText) {
  currentItems = items; renderCard = cardFn; renderDetail = detailFn;
  const term = ($("search").value || "").trim().toLowerCase();
  const shown = term ? items.filter((it) => JSON.stringify(it).toLowerCase().includes(term)) : items;
  if (!shown.length) return empty(term ? `Nothing here matches “${term}”.` : emptyText);
  $("content").innerHTML = shown.map((it) => cardFn(it)).join("");
}
$("content").addEventListener("click", (e) => {
  const c = e.target.closest("[data-item]");
  if (!c || e.target.closest("a, audio, video")) return;
  const { parts, q } = parse();
  q.set("item", c.dataset.item);
  go(parts, q);
});
$("content").addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-item]")) { e.preventDefault(); e.target.click(); }
});

const img = (src, cls = "thumb", alt = "") =>
  src ? `<img class="${cls}" src="${esc(src)}" alt="${esc(alt)}" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : "";
const why = (t) => (t ? `<div class="why">Why it's here: ${esc(t)}</div>` : "");

function videoBlock(v, trailer) {
  if (v && v.youtube_id) {
    return `<div class="video" data-yt="${esc(v.youtube_id)}"><img src="https://i.ytimg.com/vi/${esc(v.youtube_id)}/hqdefault.jpg" alt="" loading="lazy">
      <button aria-label="Play video: ${esc(v.title)}"><span>▶ Play</span></button></div>
      <div class="meta">${esc(v.title || "")}${v.channel ? " · " + esc(v.channel) : ""} · <a href="${esc(v.url)}" target="_blank" rel="noopener">YouTube</a></div>`;
  }
  if (trailer && trailer.hls) {
    const canHls = document.createElement("video").canPlayType("application/vnd.apple.mpegurl");
    if (canHls) return `<div class="video"><video controls preload="none" poster="${esc(trailer.thumb || "")}" src="${esc(trailer.hls)}"></video></div>`;
  }
  return "";
}
document.addEventListener("click", (e) => {
  const box = e.target.closest(".video[data-yt]");
  if (!box || !e.target.closest("button")) return;
  box.innerHTML = `<iframe src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(box.dataset.yt)}?autoplay=1&rel=0"
    title="Video" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe>`;
});
const linksBlock = (links) => (links && links.length
  ? `<div class="links">${links.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.name)}</a>`).join("")}</div>` : "");

/* ---------- News ---------- */
async function news(parts, q) {
  const data = await load("news");
  const tabs = Object.entries(data.regions).map(([id, r]) => [id, r.name]);
  const tab = data.regions[parts[1]] ? parts[1] : "world";
  const period = parts[2] === "week" ? "week" : "today";
  tabsRow($("tabs"), tabs, tab, (id) => go(["news", id, period], null));
  segGroups([{ label: "Period", items: NEWS_PERIODS, selected: period, onPick: (id) => go(["news", tab, id], null) }]);
  $("filters").innerHTML = "";
  note(`Ranked by how many independent outlets confirm a story, its significance, recency and source reliability. ` +
    `${num(data.article_count)} articles from ${data.outlets.length} outlets were compared.`);
  paint(data.regions[tab][period], newsCard, newsDetail, "No major stories in this window yet.");
  return [`news`, tab, period];
}
function newsCard(s) {
  return `<article class="card" tabindex="0" data-item="${esc(s.id)}">${img(s.image, "thumb", "")}
    <div class="body"><h3 class="title">${esc(s.headline)}</h3>
    <div class="meta">${esc(s.country || "")}${s.country ? " · " : ""}${ago(s.updated)} · ${new Set(s.sources.map((x) => x.name)).size} outlet${new Set(s.sources.map((x) => x.name)).size === 1 ? "" : "s"}</div>
    <div class="badges">${s.significance ? `<span class="badge ${s.significance === "major" ? "major" : ""}">${esc(s.significance)}</span>` : ""}
    ${s.video ? '<span class="badge">▶ video</span>' : ""}${s.disputed ? '<span class="badge warn">disputed claims</span>' : ""}</div>
    <p class="summary">${esc(s.summary)}</p>${why(s.why)}</div></article>`;
}
function newsDetail(s) {
  return `<div class="detail"><h2 id="sheetTitle">${esc(s.headline)}</h2>
    <div class="meta">${esc(s.country || "")} · First reported ${kuwait(s.published)} · Updated ${kuwait(s.updated)} (Kuwait)</div>
    ${img(s.image, "hero")}
    <p>${esc(s.summary)}</p>
    <div class="meta">${s.summary_by === "ai" ? "Summary written by AI from the linked articles only." : "Summary from the source article."}</div>
    ${s.disputed ? `<p class="badge warn">⚠ ${esc(s.disputed)}</p>` : ""}
    ${videoBlock(s.video)}
    <h3>Why it ranks here</h3><p>${esc(s.why)} Score ${s.score}.</p>
    <h3>Sources</h3><ul>${s.sources.map((x) => `<li><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.name)}: ${esc(x.title)}</a> <span class="meta">${kuwait(x.published)}</span></li>`).join("")}</ul></div>`;
}

/* ---------- Movies & TV ---------- */
async function movies(parts) {
  const data = await load("movies");
  const media = parts[1] === "tv" ? "tv" : "movie";
  const set = media === "tv" ? data.tv : data.movies;
  const tabs = Object.entries(set).map(([id, t]) => [id, t.name]);
  const genre = set[parts[2]] ? parts[2] : tabs[0]?.[0];
  const ranking = RANKINGS.some(([r]) => r === parts[3]) ? parts[3] : "all_time";
  tabsRow($("tabs"), tabs, genre, (id) => go(["movies", media, id, ranking], null));
  segGroups([
    { label: "Type", items: MEDIA, selected: media, onPick: (id) => go(["movies", id, (id === "tv" ? data.tv : data.movies)[genre] ? genre : "", ranking].filter(Boolean), null) },
    { label: "Ranking", items: RANKINGS, selected: ranking, onPick: (id) => go(["movies", media, genre, id], null) },
  ]);
  $("filters").innerHTML = "";
  note({ all_time: "Best of All Time: 85% rating quality (weighted by vote count) + 15% popularity.",
    recent: media === "tv" ? "Best Recent: shows with episodes aired in the last 12 months (new seasons count); 75% quality + 25% popularity."
      : "Best Recent: released in the last 12 months; 75% quality + 25% popularity.",
    trending: "Trending: last 7 days on TMDB; 70% popularity + 30% quality." }[ranking] +
    ` Streaming shown for ${data.watch_region === "KW" ? "Kuwait" : data.watch_region} where listed.`);
  paint(genre ? set[genre][ranking] : [], movieCard, movieDetail, "No titles here yet.");
  return ["movies", media, genre, ranking];
}
function movieCard(m) {
  const stream = m.watch?.stream?.length ? `Stream: ${m.watch.stream.slice(0, 2).join(", ")}${m.watch.region !== "KW" ? " (US)" : ""}` : "";
  return `<article class="card" tabindex="0" data-item="${esc(m.id)}"><div class="media-row">${img(m.poster, "thumb poster", "")}
    <div><h3 class="title"><span class="rank">#${m.rank}</span>${esc(m.title)}</h3>
    <div class="meta">${esc(m.year)} · ${esc((m.genres || []).slice(0, 3).join(", "))}</div>
    <div class="scores"><span class="score">TMDB <b>${m.rating.tmdb.toFixed(1)}</b>/10 · ${num(m.rating.votes)} votes</span></div>
    ${stream ? `<div class="meta">${esc(stream)}</div>` : ""}</div></div>
    <div class="body"><p class="summary">${esc(m.overview)}</p>${why(m.why)}</div></article>`;
}
function movieDetail(m) {
  const w = m.watch || {};
  const region = w.region === "KW" ? "Kuwait" : w.region === "US" ? "the US (not listed for Kuwait)" : null;
  return `<div class="detail"><h2 id="sheetTitle">${esc(m.title)} <span class="meta">(${esc(m.year)})</span></h2>
    <div class="split">${img(m.poster, "", m.title)}<div>
    <div class="scores"><span class="score">TMDB <b>${m.rating.tmdb.toFixed(1)}</b>/10</span><span class="score">${num(m.rating.votes)} votes</span></div>
    <div class="meta">${esc((m.genres || []).join(", "))} · ${m.released ? "Released " + kuwait(m.released, false) : ""}
    ${m.seasons_count ? ` · ${m.seasons_count} season${m.seasons_count > 1 ? "s" : ""} · ${esc(m.status || "")}` : ""}</div>
    <p>${esc(m.overview)}</p></div></div>
    ${videoBlock(m.trailer)}
    <h3>Where to watch</h3>${region ? `<p>In ${region}: ${w.stream.length ? "Stream on " + esc(w.stream.join(", ")) : "no streaming listed"}${w.rent.length ? "; rent on " + esc(w.rent.join(", ")) : ""}${w.buy.length ? "; buy on " + esc(w.buy.join(", ")) : ""}.
      ${w.link ? `<a href="${esc(w.link)}" target="_blank" rel="noopener">All options</a>` : ""}</p>` : "<p>No streaming information listed.</p>"}
    ${m.seasons?.length ? `<h3>Season ratings</h3><table class="mini"><tr><th>Season</th><th>Avg episode rating</th><th>Rated episodes</th></tr>
      ${m.seasons.map((s) => `<tr><td>${s.season} ${s.year ? `(${esc(s.year)})` : ""}</td><td>${s.rating.toFixed(1)}</td><td>${s.rated_episodes}</td></tr>`).join("")}</table>` : ""}
    ${m.top_episodes?.length ? `<h3>Highest-rated episodes</h3><ul>${m.top_episodes.map((e) => `<li>S${e.season}E${e.episode} “${esc(e.name)}” – ${e.rating.toFixed(1)} (${num(e.votes)} votes)</li>`).join("")}</ul>` : ""}
    <h3>Why it ranks here</h3><p>${esc(m.why)}</p>${linksBlock(m.links)}
    <p class="meta">Ratings and streaming data: TMDB / JustWatch.</p></div>`;
}

/* ---------- Gaming ---------- */
async function gaming(parts, q) {
  const data = await load("games");
  const tabs = Object.entries(data.genres).map(([id, g]) => [id, g.name]);
  const genre = data.genres[parts[1]] ? parts[1] : tabs[0]?.[0];
  const ranking = RANKINGS.some(([r]) => r === parts[2]) ? parts[2] : "all_time";
  const flags = (q.get("f") || "").split(",").filter(Boolean);
  tabsRow($("tabs"), tabs, genre, (id) => go(["gaming", id, ranking], flagQ(flags)));
  segGroups([{ label: "Ranking", items: RANKINGS, selected: ranking, onPick: (id) => go(["gaming", genre, id], flagQ(flags)) }]);
  chips([["indie", "Indie"], ["survival", "Survival"], ["multiplayer", "Multiplayer"]], flags, (id) => {
    const next = flags.includes(id) ? flags.filter((f) => f !== id) : [...flags, id];
    go(["gaming", genre, ranking], flagQ(next), true);
  });
  const g = data.genres[genre];
  const list = (g?.[ranking] || []).filter((x) => flags.every((f) => x.flags.includes(f)));
  note([{ all_time: "Best of All Time: 85% quality (critic + weighted user score) + 15% popularity.",
    recent: "Best Recent: released in the last 12 months; 75% quality + 25% popularity.",
    trending: "Trending: players right now and this week's change; 70% popularity + 30% quality." }[ranking], g?.notes?.stale, g?.notes?.[ranking], g?.notes?.platforms].filter(Boolean).join(" "));
  paint(list, gameCard, gameDetail, flags.length ? "No games match these filters in this list." : "No games here yet.");
  return ["gaming", genre, ranking];
}
const flagQ = (flags) => { const q = new URLSearchParams(); if (flags.length) q.set("f", flags.join(",")); return q; };
function scoreBadges(x) {
  return `<div class="scores">${x.critic ? `<span class="score">Critic <b>${x.critic.score}</b></span>` : `<span class="score">Critic –</span>`}
    ${x.user.score != null ? `<span class="score">Users <b>${Math.round(x.user.score)}%</b> · ${num(x.user.reviews)}</span>` : `<span class="score">Users –</span>`}
    <span class="badge ${x.user.confidence === "high" ? "good" : x.user.confidence === "low" ? "warn" : ""}">${esc(x.user.confidence)} confidence</span></div>`;
}
function gameCard(x) {
  return `<article class="card" tabindex="0" data-item="${esc(x.id)}">${img(x.image, "thumb", "")}
    <div class="body"><h3 class="title"><span class="rank">#${x.rank}</span>${esc(x.title)}</h3>
    <div class="meta">${x.released ? kuwait(x.released, false) : ""} · ${esc(x.platforms.join(", "))}</div>
    ${scoreBadges(x)}
    <div class="badges">${x.flags.map((f) => `<span class="badge">${esc(f)}</span>`).join("")}</div>${why(x.why)}</div></article>`;
}
function gameDetail(x) {
  return `<div class="detail"><h2 id="sheetTitle">${esc(x.title)}</h2>
    <div class="meta">${esc(x.developer || "")} · ${x.released ? "Released " + kuwait(x.released, false) : ""} · ${esc(x.platforms.join(", "))}</div>
    ${videoBlock(x.video, x.trailer) || img(x.image, "hero")}
    <h3>Scores</h3>${scoreBadges(x)}
    <p class="meta">Critic: ${x.critic ? `Metacritic${x.critic.url ? ` (<a href="${esc(x.critic.url)}" target="_blank" rel="noopener">source</a>)` : ""}` : "no critic score published"}.
    Users: ${esc(x.user.source)}${x.user.source === "Steam reviews" ? ", % positive" : ""}, weighted toward 70% when there are few ratings.</p>
    ${x.genres?.length ? `<p class="meta">Steam genres: ${esc(x.genres.join(", "))}</p>` : ""}
    <h3>Why it ranks here</h3><p>${esc(x.why)}</p>${linksBlock(x.links)}</div>`;
}

/* ---------- Music ---------- */
async function music(parts) {
  const data = await load("music");
  const tabs = Object.entries(data.genres).map(([id, g]) => [id, g.name]);
  const genre = data.genres[parts[1]] ? parts[1] : tabs[0]?.[0];
  const ranking = RANKINGS.some(([r]) => r === parts[2]) ? parts[2] : "all_time";
  tabsRow($("tabs"), tabs, genre, (id) => go(["music", id, ranking], null));
  segGroups([{ label: "Ranking", items: RANKINGS, selected: ranking, onPick: (id) => go(["music", genre, id], null) }]);
  $("filters").innerHTML = "";
  const g = data.genres[genre];
  note(g?.notes?.[ranking] || { all_time: "Best of All Time: listeners (85%) and how strongly the song is tagged with this genre (15%).",
    recent: "Best Recent: released in the last 12 months; listeners 75% + genre fit 25%.",
    trending: "Trending: growth in listens over the last 7 days (70%) + listeners (30%)." }[ranking]);
  paint(g?.[ranking] || [], songCard, songDetail, "No songs here yet.");
  return ["music", genre, ranking];
}
function songCard(s) {
  return `<article class="card" tabindex="0" data-item="${esc(s.id)}"><div class="media-row">${img(s.artwork, "thumb square", "")}
    <div><h3 class="title"><span class="rank">#${s.rank}</span>${esc(s.title)}</h3>
    <div class="meta">${esc(s.artist)}${s.released ? " · " + esc(s.released.slice(0, 4)) : ""}</div>
    <div class="scores"><span class="score"><b>${num(s.metrics.listeners)}</b> listeners</span></div></div></div>
    <div class="body">${s.preview ? `<audio controls preload="none" src="${esc(s.preview)}"></audio>` : ""}${why(s.why)}</div></article>`;
}
function songDetail(s) {
  return `<div class="detail"><h2 id="sheetTitle">${esc(s.title)}</h2>
    <div class="split">${img(s.artwork, "", s.title)}<div>
    <div class="meta">${esc(s.artist)}${s.album ? " · " + esc(s.album) : ""}${s.released ? " · Released " + kuwait(s.released, false) : ""}</div>
    <div class="scores"><span class="score"><b>${num(s.metrics.listeners)}</b> listeners</span><span class="score"><b>${num(s.metrics.listens)}</b> listens</span>
    <span class="score">genre fit ${Math.round(s.metrics.genre_fit * 100)}%</span></div>
    <p class="meta">Listener counts: ${esc(s.metrics.source)}.</p></div></div>
    ${s.preview ? `<h3>Preview</h3><audio controls preload="none" src="${esc(s.preview)}"></audio>` : ""}
    ${videoBlock(s.video)}
    <h3>Why it ranks here</h3><p>${esc(s.why)}</p>${linksBlock(s.links)}</div>`;
}

/* ---------- Sports ---------- */
async function sports(parts, q) {
  const data = await load("sports");
  const tabs = Object.entries(data.sports).map(([id, s]) => [id, `${s.icon} ${s.name}`]);
  const sport = data.sports[parts[1]] ? parts[1] : tabs[0]?.[0];
  const s = data.sports[sport];
  let view = SPORT_VIEWS.some(([v]) => v === parts[2]) ? parts[2] : (s.highlights.length ? "highlights" : "results");
  const league = q.get("league") || "";
  const lq = (l) => { const n = new URLSearchParams(); if (l) n.set("league", l); return n; };
  tabsRow($("tabs"), tabs, sport, (id) => go(["sports", id], null));
  segGroups([{ label: "View", items: SPORT_VIEWS, selected: view, onPick: (id) => go(["sports", sport, id], lq(league)) }]);
  chips([["", "All leagues"], ...s.leagues.map((l) => [l, l])], [league], (id) => go(["sports", sport, view], lq(id), true));
  const list = s[view].filter((x) => !league || x.league === league);
  note({ highlights: "Official highlight videos from league channels in the last 7 days, most watched first.",
    results: "The week's biggest results and events: results, titles, records and major incidents first, confirmed by more outlets.",
    latest: "Newest reports from the last 24 hours." }[view] + (view === "highlights" && !s.highlights.length ? " Highlights appear once the YouTube connection is active." : ""));
  paint(list, view === "highlights" ? highlightCard : sportCard, view === "highlights" ? highlightDetail : sportDetail,
    league ? `Nothing for ${league} in this view right now.` : "Nothing in this view right now.");
  return ["sports", sport, view];
}
function highlightCard(v) {
  return `<article class="card" tabindex="0" data-item="${esc(v.youtube_id)}">${img(`https://i.ytimg.com/vi/${v.youtube_id}/hqdefault.jpg`, "thumb", "")}
    <div class="body"><h3 class="title">${esc(v.title)}</h3><div class="meta">${esc(v.channel)} · ${ago(v.published)} · ${num(v.views)} views</div>
    ${v.league ? `<div class="badges"><span class="badge">${esc(v.league)}</span></div>` : ""}</div></article>`;
}
function highlightDetail(v) {
  return `<div class="detail"><h2 id="sheetTitle">${esc(v.title)}</h2>${videoBlock(v)}<p>${esc(v.why)}</p></div>`;
}
function sportCard(e) {
  return `<article class="card" tabindex="0" data-item="${esc(e.id)}">${img(e.image, "thumb", "")}
    <div class="body"><h3 class="title">${esc(e.title)}</h3><div class="meta">${e.league ? esc(e.league) + " · " : ""}${ago(e.published)}</div>
    <p class="summary">${esc(e.summary)}</p>${why(e.why)}</div></article>`;
}
function sportDetail(e) {
  return `<div class="detail"><h2 id="sheetTitle">${esc(e.title)}</h2><div class="meta">${e.league ? esc(e.league) + " · " : ""}${kuwait(e.published)} (Kuwait)</div>
    ${img(e.image, "hero")}<p>${esc(e.summary)}</p><h3>Why it's here</h3><p>${esc(e.why)}.</p>
    <h3>Sources</h3><ul>${e.sources.map((x) => `<li><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.name)}: ${esc(x.title)}</a></li>`).join("")}</ul></div>`;
}

/* ---------- global search ---------- */
async function search(parts, q) {
  const term = (q.get("q") || "").trim();
  $("tabs").innerHTML = ""; $("subtabs").innerHTML = ""; $("filters").innerHTML = "";
  note(term ? `Results for “${term}” across all sections.` : "Type in the search box and press Enter.");
  if (!term) { empty(""); return ["search"]; }
  const t = term.toLowerCase();
  const hit = (o) => JSON.stringify(o).toLowerCase().includes(t);
  const out = [];
  const add = (label, route, item, title) => out.push({ label, route, item, title });
  const [n, m, g, mu, sp] = await Promise.allSettled(SECTIONS.map((s) => load(s.file)));
  if (n.value) for (const [rid, r] of Object.entries(n.value.regions)) for (const p of ["today", "week"]) for (const s of r[p]) if (hit(s)) add(`🌍 ${r.name}`, ["news", rid, p], s.id, s.headline);
  if (m.value) for (const [media, set] of [["movie", m.value.movies], ["tv", m.value.tv]]) for (const [gid, gg] of Object.entries(set)) for (const [rk] of RANKINGS) for (const x of gg[rk]) if (hit(x.title)) add(`🎬 ${gg.name}`, ["movies", media, gid, rk], x.id, x.title);
  if (g.value) for (const [gid, gg] of Object.entries(g.value.genres)) for (const [rk] of RANKINGS) for (const x of gg[rk]) if (hit(x.title)) add(`🎮 ${gg.name}`, ["gaming", gid, rk], x.id, x.title);
  if (mu.value) for (const [gid, gg] of Object.entries(mu.value.genres)) for (const [rk] of RANKINGS) for (const x of gg[rk]) if (hit(x.title + " " + x.artist)) add(`🎵 ${gg.name}`, ["music", gid, rk], x.id, `${x.artist} – ${x.title}`);
  if (sp.value) for (const [sid, s] of Object.entries(sp.value.sports)) for (const v of ["results", "latest"]) for (const x of s[v]) if (hit(x.title)) add(`🏆 ${s.name}`, ["sports", sid, v], x.id, x.title);
  const seen = new Set();
  const rows = out.filter((r) => !seen.has(r.title) && seen.add(r.title)).slice(0, 80);
  $("content").innerHTML = rows.length ? rows.map((r) => {
    const qq = new URLSearchParams(); qq.set("item", r.item);
    return `<a class="card" href="${esc(href(r.route, qq))}" style="text-decoration:none;color:inherit"><div class="body"><div class="meta">${esc(r.label)}</div><h3 class="title">${esc(r.title)}</h3></div></a>`;
  }).join("") : `<p class="empty">No matches for “${esc(term)}”.</p>`;
  return ["search"];
}

/* ---------- detail sheet ---------- */
let sheetOpenedHere = false;
function openSheet(itemId) {
  const it = currentItems.find((x) => String(x.id ?? x.youtube_id) === itemId);
  if (!it || !renderDetail) return closeSheet(false);
  $("sheetBody").innerHTML = renderDetail(it);
  $("sheet").hidden = false;
  document.body.style.overflow = "hidden";
  $("sheetClose").focus();
}
function closeSheet(navigate = true) {
  if ($("sheet").hidden) return;
  $("sheet").hidden = true;
  $("sheetBody").innerHTML = "";   // stops any playing video
  document.body.style.overflow = "";
  if (navigate) {
    const { parts, q } = parse();
    if (q.has("item")) {
      if (sheetOpenedHere) history.back();
      else { q.delete("item"); history.replaceState(null, "", href(parts, q)); }
    }
  }
}
$("sheetClose").onclick = () => closeSheet();
$("sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") closeSheet(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

/* ---------- main router ---------- */
const VIEWS = { news, movies, gaming, music, sports, search };
let lastListKey = "";
let firstRoute = true;
async function route() {
  const { parts, q } = parse();
  const section = VIEWS[parts[0]] ? parts[0] : "news";
  const item = q.get("item");
  const listQ = new URLSearchParams(q); listQ.delete("item");
  const listKey = section + "|" + parts.join("/") + "|" + listQ.toString();
  renderSections(section === "search" ? null : section);
  if (listKey !== lastListKey) {
    closeSheet(false);
    lastListKey = listKey;
    skeleton();
    if (section !== "search") await showMeta(section);
    try {
      const canonical = await VIEWS[section](parts, q);
      if (section !== "search" && canonical) {
        const want = href(canonical.filter(Boolean), listQ);
        if (!item && location.hash !== want) history.replaceState(null, "", want);
        store.set("last:" + section, want);
        lastListKey = section + "|" + canonical.filter(Boolean).join("/") + "|" + listQ.toString();
      }
    } catch (e) {
      const m = meta?.sections?.[SECTIONS.find((x) => x.id === section)?.file];
      empty(m && !m.updated_at && /not set/.test(m.error || "")
        ? "This section starts as soon as its data source is connected (API key pending). Everything else is live."
        : "This section couldn't be loaded. Check your connection and try again.");
      console.warn(e);
    }
  }
  if (item) { sheetOpenedHere = !firstRoute; openSheet(item); } else closeSheet(false);
  firstRoute = false;
}
window.addEventListener("popstate", route);
window.addEventListener("hashchange", route);

/* ---------- search box + theme ---------- */
$("searchBtn").onclick = () => {
  const w = $("searchWrap"); w.hidden = !w.hidden;
  if (!w.hidden) $("search").focus();
};
$("search").addEventListener("input", () => { if (renderCard && parse().parts[0] !== "search") paint(currentItems, renderCard, renderDetail, ""); });
$("search").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const term = $("search").value.trim();
  if (!term) return;
  const q = new URLSearchParams(); q.set("q", term);
  $("search").value = "";
  go(["search"], q);
});
const applyTheme = (t) => {
  document.documentElement.dataset.theme = t;
  document.querySelector('meta[name="theme-color"]').content = t === "light" ? "#f6f7f9" : "#0e1116";
};
applyTheme(store.get("theme") || "dark");
$("themeBtn").onclick = () => {
  const t = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  applyTheme(t); store.set("theme", t);
};

route();
