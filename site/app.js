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
// Owner, 3 Nov 2026: Top 10 on every News tab.
const NEWS_PERIODS = [["today", "Top 10 Daily"], ["week", "Top 10 Weekly"], ["month", "Top 10 Monthly"]];
// Topic tabs on the first row (no label), then World and the regions on the second (owner, 3 Nov 2026).
const NEWS_TOPICS = ["business", "tech", "entertainment", "science", "health", "climate"];
const NEWS_TOPIC_NOTES = { business: " about business, the economy, markets and companies", tech: " about technology, tech companies, AI and the internet",
  entertainment: " about film, TV, music, celebrities and the arts", science: " about discoveries, research, space, physics, biology and breakthroughs",
  health: " about medicine, diseases, treatments, medical research and public health",
  climate: " about weather events, natural disasters, climate change, the environment and climate policy" };
const SPORT_VIEWS = [["highlights", "Top Highlights"], ["results", "Major Results & Events"], ["latest", "Latest (24h)"]];
const MEDIA = [["tv", "TV Shows"], ["movie", "Movies"]];   // TV first (owner, 1 Nov)
const STALE_HOURS = 30;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

// Owner, 3 Nov 2026: every fresh open starts on the default views (News World Top 10 Daily; Movies & TV
// alternates TV Shows / Movies per open; Gaming and Music All, Best of All Time; Sports Upcoming, all
// leagues). Views picked during a visit are remembered only for that visit, never across opens.
const lastView = {};
try { Object.keys(localStorage).filter((k) => k.startsWith("last:")).forEach((k) => localStorage.removeItem(k)); } catch { /* blocked */ }
const firstMedia = (() => {
  const next = store.get("mediaNext") === "movie" ? "movie" : "tv";
  store.set("mediaNext", next === "tv" ? "movie" : "tv");
  return next;
})();
if (!/[?&]item=/.test(location.hash) && location.hash && location.hash !== "#/news") history.replaceState(null, "", "#/news");

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

/* ---------- time zone ---------- */
// The viewer's zone: the one their device sets from its location (saved once location is allowed).
const deviceTz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kuwait"; } catch { return "Asia/Kuwait"; } })();
const viewerTz = () => store.get("tz") || deviceTz;
const city = (tz) => (tz || "").split("/").pop().replace(/_/g, " ");
const tzLabel = () => `${city(viewerTz())} time`;

/* ---------- formatting ---------- */
// (named kuwait() from when the site was Kuwait-only; it now formats in the viewer's zone)
const kuwait = (iso, withTime = true) => {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00Z" : iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString("en-GB", { timeZone: iso.length === 10 ? "UTC" : viewerTz(), day: "numeric", month: "short", year: "numeric",
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
    `<a class="sec" href="${esc(lastView[s.id] || "#/" + s.id)}" ${s.id === active ? 'aria-current="page"' : ""}>${s.icon} ${esc(s.name)}</a>`).join("");
}
function tabsRow(el, items, selected, onPick, role = "tab") {
  el.classList.remove("tabs--rows");
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
// Several rows of tabs in one tab bar (News: topics, then World + regions).
function tabsRows(el, rows, selected, onPick) {
  el.classList.add("tabs--rows");
  el.innerHTML = rows.filter((r) => r.length).map((items) => `<div class="tabrow" role="tablist">` + items.map(([id, label]) =>
    `<button class="tab" role="tab" aria-selected="${id === selected}" data-id="${esc(id)}">${esc(label)}</button>`).join("") + `</div>`).join("");
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
function note(text, label = "Ranked by") {
  const n = $("note"); n.hidden = !text;
  n.innerHTML = text ? (label ? `<b>${esc(label)}:</b> ` : "") + esc(text) : "";
}

/* Centered section header + what the whole section is ranked by */
const ABOUT = {
  news: "Stories are grouped across BBC, Reuters, AP, Al Jazeera, DW, France 24 and CNBC, then ranked by how many independent outlets confirm them (35%), significance (30%), recency (20%) and source reliability (15%). Duplicates are merged into one story.",
  movies: "Everything comes from IMDb: which titles exist, their genres, ratings and votes. Every list shows only titles rated 7.5+ out of 10, and ratings are weighted by number of votes, so a title with a few votes can't top the list.",
  gaming: "Ranked only with Steam data. Best of All Time: one third review score, one third players right now, one third number of reviews. Best Recent (last 5 years): one third review score, one third number of reviews, one third players right now. Trending: 50% players right now + 50% review score. Review scores are weighted so a game with few reviews can't top the list, and counts are scaled so the biggest games don't win on size alone. Games not sold on Steam (Nintendo and PlayStation exclusives) aren't listed.",
  music: "Ranked only by YouTube: views of each song's official upload (artist channel, VEVO or Topic). Last.fm, Deezer charts and MusicBrainz only decide which genre a song belongs to, never its rank.",
  sports: "Highlights are official league-channel videos ranked by views. Results and events are ranked by importance (results, titles, records, major incidents), how many outlets report them, and recency.",
};
/* Where each section's facts come from, shown in a banner under its title */
const SOURCES = {
  news: "BBC, Reuters, AP, Al Jazeera, DW, France 24, CNBC, ABC News Australia, RNZ and The Guardian. Every story links to the original reports; AI only writes the short summary.",
  movies: "IMDb's official daily data (titles, genres, ratings and votes). TMDB only adds posters, trailers and where to stream (JustWatch), and says which titles are anime or superhero.",
  gaming: "Steam (official player reviews, live player counts and charts) and RAWG (to find games in each genre and which consoles they're also on).",
  music: "YouTube (view counts of each song's official video or audio: the only ranking), Last.fm, Deezer's streaming charts, MusicBrainz and ListenBrainz (which songs belong to each genre) and Apple's iTunes (artwork and previews).",
  sports: "Official league YouTube channels (Premier League, NBA, NFL, UFC, Formula 1 and more), BBC Sport, ESPN, Yahoo Sports, CBS Sports, Motorsport.com, HLTV, The Guardian, and each league's established outlets.",
};
function heading(section, crumbs) {
  const s = SECTIONS.find((x) => x.id === section);
  $("pageTitle").textContent = s ? `${s.icon} ${s.name}` : "Search";
  $("pageSub").textContent = crumbs.filter(Boolean).join(" · ");
  // a picture pointing (or looking) up at the choices above the results
  $("pointer").innerHTML = SECTIONS.some((x) => x.id === section) ? `<img src="img/pointers/${section}.jpg" alt="" decoding="async">` : "";
  // Movies & TV: SeriesGraph, episode-by-episode ratings for any show (owner, 2 Nov 2026)
  $("sectionLink").innerHTML = section === "movies"
    ? `<a href="https://seriesgraph.com/" target="_blank" rel="noopener"><img src="img/links/seriesgraph.jpg" alt="SeriesGraph episode ratings chart" loading="lazy" decoding="async">
       <span>📊 SeriesGraph: see every episode's rating for any TV show</span></a>` : "";
  $("sectionSources").innerHTML = SOURCES[section] ? `<b>Sources:</b> ${esc(SOURCES[section])}` : "";
  $("sectionAbout").innerHTML = ABOUT[section] ? `<b>How this section is ranked:</b> ${esc(ABOUT[section])}` : "";
  document.title = [crumbs.filter(Boolean).join(" · "), s?.name, "Discover"].filter(Boolean).join(" – ");
}
const label = (pairs, id) => (pairs.find(([k]) => k === id) || [, ""])[1];
function skeleton(n = 6) { $("content").innerHTML = Array.from({ length: n }, () => '<div class="skeleton"></div>').join(""); }
function empty(text) { $("content").innerHTML = `<p class="empty">${esc(text)}</p>`; }

// Every section updates together once a day, live by 12:00 noon Kuwait (09:00 UTC); the run starts
// about 10:30 Kuwait (07:30 UTC). Shown under "Updated" (owner, 3 Nov 2026).
function nextUpdateText(updatedAt) {
  const now = new Date();
  const todayNoon = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 9, 0));
  const todayStart = new Date(todayNoon.getTime() - 90 * 60 * 1000);
  const doneToday = updatedAt && new Date(updatedAt) >= todayStart;
  if (now >= todayNoon && !doneToday) return "Next update: any minute now (today's is running late)";
  const next = now < todayNoon ? todayNoon : new Date(todayNoon.getTime() + 24 * 3600 * 1000);
  return `Next update: ${kuwait(next.toISOString())} (${tzLabel()})`;
}

let meta = null;
async function showMeta(section) {
  try { meta = meta || await load("meta"); } catch { meta = { sections: {} }; }
  const file = SECTIONS.find((s) => s.id === section)?.file;
  const m = meta.sections?.[file];
  const u = $("updated");
  u.textContent = m?.updated_at ? `Updated ${kuwait(m.updated_at)} (${tzLabel()})` : "";
  const nx = nextUpdateText(m?.updated_at);
  if (nx) { u.appendChild(document.createElement("br")); u.appendChild(document.createTextNode(nx)); }
  locationBanner();
  const st = $("stale");
  const old = m?.updated_at && (Date.now() - new Date(m.updated_at)) / 36e5 > STALE_HOURS;
  if (m?.updated_at && (m.ok === false || old)) {
    st.hidden = false;
    st.textContent = `This section shows data from ${kuwait(m.updated_at)} (${ago(m.updated_at)}). The latest refresh ` +
      (m.ok === false ? "could not get new data; it will retry at the next daily update." : "is late.");
  } else st.hidden = true;
}

/* ---------- location banner: show every time in the viewer's local time ---------- */
// Stays at the top until location is allowed. The coordinates themselves are never read or sent: once
// allowed, the time zone the device sets from its location is used. The daily update itself is fixed
// at 12:00 noon Kuwait (09:00 UTC), the best single time for Kuwait, Europe, East Asia and the US.
let locating = false;
function locate(prompt) {
  if (!navigator.geolocation || locating) return;
  locating = true;
  if (prompt) bannerHtml("📍 Finding your time zone…");
  navigator.geolocation.getCurrentPosition(() => {
    // phones keep their time zone in step with their location; re-read it (it may have changed since load)
    let tz = deviceTz;
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || deviceTz; } catch { /* keep device zone */ }
    store.set("tz", tz); store.set("locAllowed", "1");
    locating = false;
    route();
  }, (err) => {
    locating = false;
    if (err.code === err.PERMISSION_DENIED) { store.set("locAllowed", ""); bannerAsk(true); }
    else if (prompt) bannerAsk(false, "Couldn't get your location just now. Try again in a moment.");
  }, { enableHighAccuracy: false, timeout: 15000, maximumAge: 6 * 3600e3 });
}
function bannerHtml(html) { const b = $("locBanner"); b.hidden = false; b.innerHTML = `<div class="banner-in">${html}</div>`; }
function bannerAsk(blocked, extra = "") {
  bannerHtml(`<p><b>📍 Allow location to see every time on this site in your local time.</b></p>
    <p>Only your time zone is used; your exact location is never sent or stored anywhere.
    The site updates every day at 12:00 noon Kuwait time (09:00 UTC).</p>
    ${blocked ? `<p class="warn-text">Location is blocked for this site. Allow it in your browser's site settings, then tap the button.</p>` : ""}
    ${extra ? `<p class="warn-text">${esc(extra)}</p>` : ""}
    <button id="locBtn" class="banner-btn">Allow location</button>`);
  $("locBtn").onclick = () => locate(true);
}
async function locationBanner() {
  if (!navigator.geolocation) { $("locBanner").hidden = true; return; }
  let perm = null;
  try { perm = (await navigator.permissions?.query({ name: "geolocation" }))?.state || null; } catch { /* Safari: unknown */ }
  const allowed = perm === "granted" || (perm === null && store.get("locAllowed") === "1");
  if (perm === "denied") return bannerAsk(true);
  if (!allowed) return bannerAsk(false);
  if (!locating && !locationBanner.checked) { locationBanner.checked = true; locate(false); }   // travelling: re-check once per visit
  $("locBanner").hidden = true;
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
  const period = ["week", "month"].includes(parts[2]) ? parts[2] : "today";
  const topicRow = NEWS_TOPICS.filter((id) => data.regions[id]).map((id) => [id, data.regions[id].name]);
  const placeRow = tabs.filter(([id]) => !NEWS_TOPICS.includes(id)).sort(([a], [b]) => (a === "world" ? -1 : b === "world" ? 1 : 0));
  tabsRows($("tabs"), [topicRow, placeRow], tab, (id) => go(["news", id, period], null));
  const periods = NEWS_PERIODS;
  segGroups([{ label: "Period", items: periods, selected: period, onPick: (id) => go(["news", tab, id], null) }]);
  $("filters").innerHTML = "";
  heading("news", [data.regions[tab].name, label(periods, period)]);
  note(`${data.regions[tab].name} stories from the ${{ today: "last 24 hours", week: "last 7 days", month: "last 30 days" }[period]}` +
    (NEWS_TOPIC_NOTES[tab] || (tab === "world" ? " from every region" : "")) +
    (period === "month" ? ", ranked by how many outlets covered it (30%), significance (30%), how many days it stayed in the news (20%), source reliability (15%) and recency (5%). " :
      `, ranked by outlets confirming it, significance, recency and source reliability. `) +
    `${num(data.article_count)} articles from ${data.outlets.length} outlets were compared today.`);
  paint(data.regions[tab][period], newsCard, newsDetail, "No major stories in this window yet.");
  return [`news`, tab, period];
}
function newsCard(s) {
  return `<article class="card" tabindex="0" data-item="${esc(s.id)}">${img(s.image, "thumb", "")}
    <div class="body"><h3 class="title">${esc(s.headline)}</h3>
    <div class="meta">${esc(s.country || "")}${s.country ? " · " : ""}${ago(s.updated)} · ${new Set(s.sources.map((x) => x.name)).size} outlet${new Set(s.sources.map((x) => x.name)).size === 1 ? "" : "s"}</div>
    <div class="badges">${s.significance ? `<span class="badge ${s.significance === "major" ? "major" : ""}">${esc(s.significance)}</span>` : ""}
    ${s.older ? '<span class="badge warn">older than 24 h</span>' : ""}${s.video ? '<span class="badge">▶ video</span>' : ""}${s.disputed ? '<span class="badge warn">disputed claims</span>' : ""}</div>
    <p class="summary">${esc(s.summary)}</p>${why(s.why)}</div></article>`;
}
function newsDetail(s) {
  return `<div class="detail"><h2 id="sheetTitle">${esc(s.headline)}</h2>
    <div class="meta">${esc(s.country || "")} · First reported ${kuwait(s.published)} · Updated ${kuwait(s.updated)} (${tzLabel()})</div>
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
  const media = parts[1] === "movie" || parts[1] === "tv" ? parts[1] : firstMedia;   // alternates per open (owner, 3 Nov)
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
  heading("movies", [label(MEDIA, media), genre && set[genre].name, label(RANKINGS, ranking)]);
  note(`${genre ? set[genre].name + " " : ""}${media === "tv" ? "TV shows" : "movies"}: ` + { all_time: "Best of All Time: rated 7.5+; ordered by IMDb rating, like IMDb's Top Rated (titles with few votes are weighted down so they can't jump to the top).",
    recent: media === "tv" ? "Best Recent: shows that premiered in the last 5 years with new episodes this year or last year, rated 7.5+; 75% IMDb rating + 25% IMDb votes."
      : "Best Recent: released this year or last year, rated 7.5+; 75% IMDb rating + 25% IMDb votes.",
    trending: "Trending: most IMDb votes gained per day this week, rated 7.5+; 70% votes gained + 30% IMDb rating." }[ranking] +
    (genre && set[genre].notes?.[ranking] ? " " + set[genre].notes[ranking] : "") +
    ` Streaming shown for ${data.watch_region === "KW" ? "Kuwait" : data.watch_region} where listed.`);
  paint(genre ? set[genre][ranking] : [], movieCard, movieDetail, "No titles here yet.");
  return ["movies", media, genre, ranking];
}
const ratingOf = (m) => m.rating.value ?? 0;
function movieCard(m) {
  const stream = m.watch?.stream?.length ? `Stream: ${m.watch.stream.slice(0, 2).join(", ")}${m.watch.region !== "KW" ? " (US)" : ""}` : "";
  return `<article class="card" tabindex="0" data-item="${esc(m.id)}"><div class="media-row">${img(m.poster, "thumb poster", "")}
    <div><h3 class="title"><span class="rank">#${m.rank}</span>${esc(m.title)}</h3>
    <div class="meta">${esc(m.year)} · ${esc((m.genres || []).slice(0, 3).join(", "))}</div>
    <div class="scores"><span class="score">${esc(m.rating.source)} <b>${ratingOf(m).toFixed(1)}</b>/10 · ${num(m.rating.votes)} votes</span></div>
    ${stream ? `<div class="meta">${esc(stream)}</div>` : ""}</div></div>
    <div class="body"><p class="summary">${esc(m.overview)}</p>${why(m.why)}</div></article>`;
}
function movieDetail(m) {
  const w = m.watch || {};
  const region = w.region === "KW" ? "Kuwait" : w.region === "US" ? "the US (not listed for Kuwait)" : null;
  return `<div class="detail"><h2 id="sheetTitle">${esc(m.title)} <span class="meta">(${esc(m.year)})</span></h2>
    <div class="split">${img(m.poster, "", m.title)}<div>
    <div class="scores"><span class="score">${esc(m.rating.source)} <b>${ratingOf(m).toFixed(1)}</b>/10</span><span class="score">${num(m.rating.votes)} votes</span>
    </div>
    <div class="meta">${esc((m.genres || []).join(", "))} · ${m.released ? "Released " + kuwait(m.released, false) : ""}
    ${m.seasons_count ? ` · ${m.seasons_count} season${m.seasons_count > 1 ? "s" : ""} · ${esc(m.status || "")}` : ""}</div>
    <p>${esc(m.overview)}</p></div></div>
    ${videoBlock(m.trailer)}
    <h3>Where to watch</h3>${region ? `<p>In ${region}: ${w.stream.length ? "Stream on " + esc(w.stream.join(", ")) : "no streaming listed"}${w.rent.length ? "; rent on " + esc(w.rent.join(", ")) : ""}${w.buy.length ? "; buy on " + esc(w.buy.join(", ")) : ""}.
      ${w.link ? `<a href="${esc(w.link)}" target="_blank" rel="noopener">All options</a>` : ""}</p>` : "<p>No streaming information listed.</p>"}
    ${m.seasons?.length ? `<h3>Season ratings${m.ratings_source ? " (" + esc(m.ratings_source) + ")" : ""}</h3><table class="mini"><tr><th>Season</th><th>Avg episode rating</th><th>Rated episodes</th></tr>
      ${m.seasons.map((s) => `<tr><td>${s.season} ${s.year ? `(${esc(s.year)})` : ""}</td><td>${s.rating.toFixed(1)}</td><td>${s.rated_episodes}</td></tr>`).join("")}</table>` : ""}
    ${m.top_episodes?.length ? `<h3>Highest-rated episodes${m.ratings_source ? " (" + esc(m.ratings_source) + ")" : ""}</h3><ul>${m.top_episodes.map((e) =>
      `<li>S${e.season}E${e.episode} ${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">“${esc(e.name)}”</a>` : `“${esc(e.name)}”`} – ${Number(e.rating).toFixed(1)} (${num(e.votes)} votes)</li>`).join("")}</ul>` : ""}
    <h3>Why it ranks here</h3><p>${esc(m.why)}</p>${linksBlock(m.links)}
    <p class="meta">Ratings: ${m.rating.source === "IMDb" ? "IMDb (official daily data)" : "TMDB"}. Details and streaming: TMDB / JustWatch.</p></div>`;
}

/* ---------- Gaming ---------- */
async function gaming(parts, q) {
  const data = await load("games");
  const tabs = Object.entries(data.genres).map(([id, g]) => [id, g.name]);
  const genre = data.genres[parts[1]] ? parts[1] : tabs[0]?.[0];
  const ranking = RANKINGS.some(([r]) => r === parts[2]) ? parts[2] : "all_time";
  // Story mode / Online filters removed (owner, 2 Nov 2026: Steam's mode data too inaccurate)
  tabsRow($("tabs"), tabs, genre, (id) => go(["gaming", id, ranking], null));
  segGroups([{ label: "Ranking", items: RANKINGS, selected: ranking, onPick: (id) => go(["gaming", genre, id], null) }]);
  $("filters").innerHTML = "";
  const g = data.genres[genre];
  const list = (g?.[ranking] || []).slice(0, 30);   // Top 30 (owner, 2 Nov 2026)
  heading("gaming", [g?.name, label(RANKINGS, ranking)]);
  note([`${g?.name || ""} games:`, { all_time: "Best of All Time: one third Steam review score (weighted so a few reviews can't top it), one third players on Steam right now, one third number of reviews.",
    recent: "Best Recent: released in the last 5 years; one third Steam review score, one third number of reviews, one third players on Steam right now.",
    trending: "Trending: 50% players on Steam right now + 50% Steam review score." }[ranking], g?.notes?.stale, g?.notes?.[ranking], g?.notes?.platforms].filter(Boolean).join(" "));
  paint(list, gameCard, gameDetail, "No games here yet.");
  return ["gaming", genre, ranking];
}
function scoreBadges(x) {
  const u = x.user;
  return `<div class="scores">${u && u.score != null ? `<span class="score">Steam reviews <b>${Math.round(u.score)}%</b> · ${num(u.reviews)}</span>` : ""}
    ${x.players_now != null ? `<span class="score">Playing now <b>${num(x.players_now)}</b> <span class="meta">Steam</span></span>` : ""}
    ${!(u && u.score != null) ? '<span class="badge">no reviews yet</span>'
      : u ? `<span class="badge ${u.confidence === "high" ? "good" : u.confidence === "low" ? "warn" : ""}">${esc(u.confidence)} confidence</span>` : ""}</div>`;
}
function gameCard(x) {
  return `<article class="card" tabindex="0" data-item="${esc(x.id)}">${img(x.image, "thumb", "")}
    <div class="body"><h3 class="title"><span class="rank">#${x.rank}</span>${esc(x.title)}</h3>
    <div class="meta">${x.released ? kuwait(x.released, false) : ""} · ${esc(x.platforms.join(", "))}</div>
    ${scoreBadges(x)}
    ${why(x.why)}</div></article>`;
}
function gameDetail(x) {
  return `<div class="detail"><h2 id="sheetTitle">${esc(x.title)}</h2>
    <div class="meta">${esc(x.developer || "")} · ${x.released ? "Released " + kuwait(x.released, false) : ""} · ${esc(x.platforms.join(", "))}</div>
    ${videoBlock(x.video, x.trailer) || img(x.image, "hero")}
    <h3>Scores</h3>${scoreBadges(x)}
    <p class="meta">Steam reviews: % positive, weighted toward 70% when there are few reviews. Playing now: Steam's official live count at the daily update.</p>
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
  heading("music", [g?.name, label(RANKINGS, ranking)]);
  note(`${g?.name || ""}: ` + ({ all_time: "Best of All Time: total YouTube views of the official video.",
    recent: "Best Recent: ranked only by YouTube views gained per week (this week's measured gain).",
    trending: "Trending: released in the last 12 months; total YouTube views." }[ranking]) +
    (g?.notes?.[ranking] ? " " + g.notes[ranking] : ""));
  paint(g?.[ranking] || [], songCard, songDetail, "No songs here yet.");
  return ["music", genre, ranking];
}
function songCard(s) {
  return `<article class="card" tabindex="0" data-item="${esc(s.id)}"><div class="media-row">${img(s.artwork, "thumb square", "")}
    <div><h3 class="title"><span class="rank">#${s.rank}</span>${esc(s.title)}</h3>
    <div class="meta">${esc(s.artist)}${s.released ? " · " + esc(s.released.slice(0, 4)) : ""}</div>
    <div class="scores"><span class="score"><b>${num(s.metrics.views)}</b> ${esc(s.metrics.source)} views</span></div></div></div>
    <div class="body">${s.preview ? `<audio controls preload="none" src="${esc(s.preview)}"></audio>` : ""}${why(s.why)}</div></article>`;
}
function songDetail(s) {
  return `<div class="detail"><h2 id="sheetTitle">${esc(s.title)}</h2>
    <div class="split">${img(s.artwork, "", s.title)}<div>
    <div class="meta">${esc(s.artist)}${s.album ? " · " + esc(s.album) : ""}${s.released ? " · Released " + kuwait(s.released, false) : ""}</div>
    <div class="scores"><span class="score"><b>${num(s.metrics.views)}</b> views</span>
    </div>
    <p class="meta">Views: ${esc(s.metrics.source)}, official upload${s.metrics.channel ? " on " + esc(s.metrics.channel) : ""}, all time.</p></div></div>
    ${s.preview ? `<h3>Preview</h3><audio controls preload="none" src="${esc(s.preview)}"></audio>` : ""}
    ${videoBlock(s.video)}
    <h3>Why it ranks here</h3><p>${esc(s.why)}</p>${linksBlock(s.links)}</div>`;
}

/* ---------- Sports ---------- */
async function sports(parts, q) {
  const data = await load("sports");
  const up = data.upcoming;
  const tabs = [...(up ? [["upcoming", `${up.icon} ${up.name}`]] : []), ...Object.entries(data.sports).map(([id, s]) => [id, `${s.icon} ${s.name}`])];
  if (up && (parts[1] === "upcoming" || !parts[1])) return upcomingView(up, tabs, q);
  const sport = data.sports[parts[1]] ? parts[1] : tabs[0]?.[0];
  const s = data.sports[sport];
  let view = SPORT_VIEWS.some(([v]) => v === parts[2]) ? parts[2] : "highlights";   // always first (owner, 3 Nov)
  const league = q.get("league") || "";
  const lq = (l) => { const n = new URLSearchParams(); if (l) n.set("league", l); return n; };
  tabsRow($("tabs"), tabs, sport, (id) => go(["sports", id], null));
  segGroups([{ label: "View", items: SPORT_VIEWS, selected: view, onPick: (id) => go(["sports", sport, id], lq(league)) }]);
  chips([["", "All leagues"], ...s.leagues.map((l) => [l, l])], [league], (id) => go(["sports", sport, view], lq(id), true));
  const lb = league && s.by_league ? s.by_league[league] : null;
  const list = lb ? lb[view] : s[view].filter((x) => !league || x.league === league);
  const older = lb?.older?.[view];
  const olderNote = !older ? "" : older === "search"
    ? ` No official ${league} highlights recently, so these are the newest ${league} highlight videos on YouTube (the uploading channel is shown on each).`
    : ` No ${league} ${view === "latest" ? "news in the last 24 hours" : view === "highlights" ? "highlights in the last 7 days" : "results in the last 7 days"}` +
      ` (usually an off-season or a break between rounds), so this shows the most recent available, dated on each card.`;
  heading("sports", [s.name, league || "", label(SPORT_VIEWS, view)]);
  note(`${s.name}${league ? " · " + league : ""}: ` + { highlights: "Official highlight videos from league channels in the last 7 days, most watched first.",
    results: "The week's biggest results and events: results, titles, records and major incidents first, confirmed by more outlets.",
    latest: "Newest reports from the last 24 hours." }[view] + olderNote);
  paint(list, view === "highlights" ? highlightCard : sportCard, view === "highlights" ? highlightDetail : sportDetail,
    league ? (view === "latest" ? `No ${league} news in the last 24 hours.`
      : `No ${league} ${view === "highlights" ? "highlight videos" : "results or events"} in the last 7 days — usually because no ${league} matches were played this week.`)
      : "Nothing in this view right now.");
  return ["sports", sport, view];
}
// Upcoming (owner, 1 Nov 2026): the next big events of each major league, top 3 each, league named on every card
function upcomingView(up, tabs, q) {
  const league = q.get("league") || "";
  tabsRow($("tabs"), tabs, "upcoming", (id) => go(["sports", id], null));
  $("subtabs").innerHTML = "";
  chips([["", "All leagues"], ...up.leagues.map((l) => [l, l])], [league], (id) => {
    const n = new URLSearchParams(); if (id) n.set("league", id); go(["sports", "upcoming"], n, true);
  });
  // soonest first, in All leagues and in each league (owner, 2 Nov 2026)
  const list = up.events.filter((e) => !league || e.league === league).slice().sort((a, b) => String(a.when).localeCompare(String(b.when)));
  heading("sports", ["Upcoming", league || "All leagues"]);
  const off = up.off_season.filter((l) => !league || l === league);
  note("Upcoming: the next big events of each major league, top 3 each. Team leagues: the most important games in the next 7 days "
    + "(both teams' records this season, plus national TV in US leagues). F1, NASCAR, UFC, boxing, snooker and pool: the next 3 events. "
    + "Times are shown in your time zone." + (off.length ? ` Nothing scheduled yet (off-season or not announced): ${off.join(", ")}.` : ""));
  paint(list, upcomingCard, upcomingDetail, league ? `No ${league} events announced yet (off-season).` : "No upcoming events yet.");
  return ["sports", "upcoming"];
}
function upcomingCard(e) {
  const logos = e.image2 && e.image2 !== e.image
    ? `<div class="vs-logos">${img(e.image2, "vs-logo", "")}<span>vs</span>${img(e.image, "vs-logo", "")}</div>`
    : e.image ? `<div class="vs-logos">${img(e.image, "vs-logo wide", "")}</div>` : "";
  return `<article class="card" tabindex="0" data-item="${esc(e.id)}">${logos}
    <div class="body"><div class="badges"><span class="badge">${esc(e.icon)} ${esc(e.league)}</span></div>
    <h3 class="title">${esc(e.title)}</h3><div class="meta">${e.date_only ? kuwait(e.when, false) + " (time not announced)" : `${kuwait(e.when)} (${tzLabel()})`}${e.venue ? " · " + esc(e.venue) : ""}</div>${why(e.why)}</div></article>`;
}
function upcomingDetail(e) {
  return `<div class="detail"><h2 id="sheetTitle">${esc(e.title)}</h2><div class="meta">${esc(e.icon)} ${esc(e.league)} · ${e.date_only ? kuwait(e.when, false) + " (time not announced)" : `${kuwait(e.when)} (${tzLabel()})`}</div>
    ${e.venue ? `<p>Venue: ${esc(e.venue)}</p>` : ""}<h3>Why it's here</h3><p>${esc(e.why)}</p>
    ${e.url ? `<p><a href="${esc(e.url)}" target="_blank" rel="noopener">Details on ${esc(e.source)}</a></p>` : ""}</div>`;
}
function highlightCard(v) {
  return `<article class="card" tabindex="0" data-item="${esc(v.youtube_id)}">${img(`https://i.ytimg.com/vi/${v.youtube_id}/hqdefault.jpg`, "thumb", "")}
    <div class="body"><h3 class="title">${esc(v.title)}</h3><div class="meta">${esc(v.channel)} · ${kuwait(v.published, false)} (${ago(v.published)}) · ${num(v.views)} views</div>
    ${v.league ? `<div class="badges"><span class="badge">${esc(v.league)}</span></div>` : ""}</div></article>`;
}
function highlightDetail(v) {
  return `<div class="detail"><h2 id="sheetTitle">${esc(v.title)}</h2>${videoBlock(v)}<p>${esc(v.why)}</p></div>`;
}
function sportCard(e) {
  return `<article class="card" tabindex="0" data-item="${esc(e.id)}">${img(e.image, "thumb", "")}
    <div class="body"><h3 class="title">${esc(e.title)}</h3><div class="meta">${e.league ? esc(e.league) + " · " : ""}${kuwait(e.published, false)} (${ago(e.published)}) · ${esc((e.sources[0] || {}).name || "")}</div>
    <p class="summary">${esc(e.summary)}</p>${why(e.why)}</div></article>`;
}
function sportDetail(e) {
  return `<div class="detail"><h2 id="sheetTitle">${esc(e.title)}</h2><div class="meta">${e.league ? esc(e.league) + " · " : ""}${kuwait(e.published)} (${tzLabel()})</div>
    ${img(e.image, "hero")}<p>${esc(e.summary)}</p><h3>Why it's here</h3><p>${esc(e.why)}.</p>
    <h3>Sources</h3><ul>${e.sources.map((x) => `<li><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.name)}: ${esc(x.title)}</a></li>`).join("")}</ul></div>`;
}

/* ---------- global search ---------- */
async function search(parts, q) {
  const term = (q.get("q") || "").trim();
  $("tabs").innerHTML = ""; $("subtabs").innerHTML = ""; $("filters").innerHTML = "";
  heading("search", [term && `“${term}”`]);
  note(term ? `Results for “${term}” across all sections.` : "Type in the search box and press Enter.", "");
  if (!term) { empty(""); return ["search"]; }
  const t = term.toLowerCase();
  const hit = (o) => JSON.stringify(o).toLowerCase().includes(t);
  const out = [];
  const add = (label, route, item, title) => out.push({ label, route, item, title });
  const [n, m, g, mu, sp] = await Promise.allSettled(SECTIONS.map((s) => load(s.file)));
  if (n.value) for (const [rid, r] of Object.entries(n.value.regions)) for (const p of ["today", "week", "month"]) for (const s of r[p] || []) if (hit(s)) add(`🌍 ${r.name}`, ["news", rid, p], s.id, s.headline);
  if (m.value) for (const [media, set] of [["movie", m.value.movies], ["tv", m.value.tv]]) for (const [gid, gg] of Object.entries(set)) for (const [rk] of RANKINGS) for (const x of gg[rk]) if (hit(x.title)) add(`🎬 ${gg.name}`, ["movies", media, gid, rk], x.id, x.title);
  if (g.value) for (const [gid, gg] of Object.entries(g.value.genres)) for (const [rk] of RANKINGS) for (const x of gg[rk].slice(0, 30)) if (hit(x.title)) add(`🎮 ${gg.name}`, ["gaming", gid, rk], x.id, x.title);
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
        lastView[section] = want;
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
