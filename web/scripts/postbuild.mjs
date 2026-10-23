/**
 * Post-export step for Catalyst web-client hosting.
 *
 * Catalyst serves exact file paths and nothing else. Verified against the live
 * deployment:
 *
 *     /app/                    200   (root only)
 *     /app/cases/              404
 *     /app/cases               404
 *     /app/cases/index.html    200
 *     /app/nonexistent/        404   (so there is no SPA fallback either)
 *
 * Next's static export writes `cases/index.html` and links to `/app/cases/`, so
 * every route except the root was unreachable: a refresh or a shared link
 * returned Catalyst's raw `INVALID_URL_PATTERN` JSON. Only in-app navigation
 * worked, because that never touches the server.
 *
 * The one hook Catalyst offers is the 404 page (`client-package.json`, key "404",
 * which the CLI refuses to let equal `homepage`). So the 404 page is made a
 * resolver: it rewrites an extension-less path to the `index.html` that Catalyst
 * will serve, and redirects there.
 *
 * Why a redirect rather than serving the app shell from 404.html: the shell is a
 * pre-rendered export of `/`, and relying on Next's client router to re-route it
 * from an arbitrary URL is a guess about hydration behaviour. This is a guess
 * about nothing — the target file is known to exist and known to be served.
 *
 * Clean URLs survive normal use: `<Link>` navigation is client-side and never
 * hits the server, so `index.html` only appears on a cold load of a sub-route.
 */
import { copyFileSync, writeFileSync, existsSync, readFileSync, globSync } from "node:fs";

const OUT = "out";

if (!existsSync(OUT)) {
  console.error("postbuild: out/ is missing — run `next build` first");
  process.exit(1);
}

copyFileSync("client-package.json", `${OUT}/client-package.json`);

// `replace` rather than `assign`: the broken URL must not sit in history, or Back
// from the resolved page lands on it and bounces forward again.
//
// The extension test is what stops a redirect loop. `/app/cases/` has no
// extension so it resolves to `/app/cases/index.html`; that URL does have one, so
// if it is ever missing too, this page reports it instead of redirecting again.
//
// The "Return" link targets the root, which is now the sign-in page rather than
// the dashboard (that moved to /overview). That is still the right destination:
// the sign-in page forwards a visitor who already holds a valid token straight on
// to /overview, and offers everyone else the way in.
const resolver = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>KSP IntelHub</title>
<style>
  html,body{height:100%;margin:0}
  body{display:grid;place-items:center;background:#f4f4f6;color:#6b6b73;
       font:500 14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
  .box{text-align:center;max-width:32rem;padding:2rem}
  h1{font-size:1.05rem;color:#1d1d1f;margin:0 0 .5rem}
  a{color:#b07d16}
  code{background:rgba(0,0,0,.05);padding:.1rem .35rem;border-radius:.25rem;
       font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.85em}
</style>
<script>
(function () {
  var path = window.location.pathname;
  var hasExtension = /\\.[a-zA-Z0-9]+$/.test(path);
  if (!hasExtension) {
    var target = path.replace(/\\/?$/, "/") + "index.html";
    window.location.replace(target + window.location.search + window.location.hash);
  } else {
    document.addEventListener("DOMContentLoaded", function () {
      var el = document.getElementById("missing");
      if (el) el.textContent = path;
      var box = document.getElementById("box");
      if (box) box.hidden = false;
    });
  }
})();
</script>
</head>
<body>
  <div class="box" id="box" hidden>
    <h1>That page could not be found</h1>
    <p>Nothing is served at <code id="missing"></code>.</p>
    <p><a href="/app/index.html">Return to KSP IntelHub</a></p>
  </div>
</body>
</html>
`;

writeFileSync(`${OUT}/404.html`, resolver, "utf8");

/**
 * Normalise `…/index.html` back to `…/` before any other script runs.
 *
 * Next's client router derives the current route from `window.location.pathname`.
 * Landing on `/app/index.html` makes it compute `/index.html`, which matches no
 * pre-rendered route, so it renders a different tree than the HTML it is hydrating
 * and React discards the whole subtree (error #418). Reproduced exactly: `/app/`
 * hydrates clean, `/app/index.html` does not.
 *
 * This is not a rare path — the 404 resolver above sends every direct link and every
 * refresh to an `index.html` URL, because that is the only form Catalyst serves.
 *
 * Injected as the first thing in <head> so it runs before the Next bundles load and
 * the router only ever sees the clean path. `replaceState` does not refetch, and
 * every asset URL Next emits is absolute, so rewriting the path is safe.
 */
const normaliser = `<script>(function(){try{var p=location.pathname;` +
  `if(p.slice(-11)==="/index.html"){history.replaceState(null,"",` +
  `p.slice(0,-10)+location.search+location.hash);}}catch(e){}})();</script>`;

/**
 * Reload once when one of the app's own scripts or stylesheets fails to arrive.
 *
 * Catalyst's static host answers a burst of requests with 429 and a JSON body, which
 * the browser refuses to run as a script (nosniff). Lose the runtime chunk that way
 * and React never starts, so nothing inside the app — not even its error boundary —
 * can recover: the officer is left with a dead page. This listener is plain script,
 * registered before any bundle loads, and capture-phase because resource errors do
 * not bubble. At most three reloads a minute, so a file that is genuinely missing
 * cannot become a reload loop; each reload comes back under the service worker below,
 * which by then holds every file that did arrive.
 */
const recovery = `<script>(function(){window.addEventListener("error",function(e){` +
  `var t=e.target,u=t&&(t.src||t.href)||"";if(!t||(t.tagName!=="SCRIPT"&&t.tagName!=="LINK")||` +
  `u.indexOf("/_next/")<0||window.__kspReloading)return;try{var k="ksp.reloads",n=Date.now(),` +
  `r=JSON.parse(sessionStorage.getItem(k)||"[]").filter(function(x){return n-x<60000;});` +
  `if(r.length>=3)return;r.push(n);sessionStorage.setItem(k,JSON.stringify(r));}catch(x){return;}` +
  `window.__kspReloading=1;setTimeout(function(){location.reload();},1500);},true);})();</script>`;

/**
 * A service worker for the app's own static files, because the host rate-limits bursts.
 *
 * Measured after a three-minute cool-down: one cold load of /overview is 46 requests
 * and the 11th already comes back 429. A page reload repeats the same burst. So the
 * worker sends at most six /_next/static requests at a time, retries a 429 with
 * back-off, and keeps each file once it has it — the names are content hashes, so a
 * file never changes and a new build simply asks for new names. It touches nothing
 * else: API calls, pages and media go to the network as before.
 *
 * It cannot help the load that installs it (a worker controls pages opened after it),
 * which is what the one-time reload above is for: the reload comes back under it.
 */
const serviceWorker = `const CACHE = "ksp-static-v1";
const MAX = 6;
let active = 0;
const queue = [];
function gate(run) {
  return new Promise((resolve, reject) => {
    const go = () => { active++; run().then(resolve, reject).finally(() => { active--; if (queue.length) queue.shift()(); }); };
    active < MAX ? go() : queue.push(go);
  });
}
async function load(req) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  let res;
  for (let i = 0; i < 8; i++) {
    res = await gate(() => fetch(req));
    if (res.status !== 429) break;
    await new Promise((r) => setTimeout(r, Math.min(300 * 2 ** i, 2000)));
  }
  if (res.ok) cache.put(req, res.clone()).catch(() => {});
  return res;
}
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin || !url.pathname.includes("/_next/static/")) return;
  e.respondWith(load(e.request).catch(() => fetch(e.request)));
});
`;
writeFileSync(`${OUT}/sw.js`, serviceWorker, "utf8");
const register = `<script>if("serviceWorker"in navigator){navigator.serviceWorker.register("/app/sw.js",{scope:"/app/"}).catch(function(){});}</script>`;

let injected = 0;
for (const file of globSync(`${OUT}/**/*.html`)) {
  if (file.endsWith("404.html")) continue;   // the resolver must keep its own URL
  const html = readFileSync(file, "utf8");
  if (html.includes("/index.html\"")) continue;
  const at = html.indexOf("<head>");
  if (at === -1) continue;
  writeFileSync(file, html.slice(0, at + 6) + normaliser + recovery + register + html.slice(at + 6), "utf8");
  injected++;
}

console.log(`postbuild: client-package.json copied, 404.html written as a path ` +
            `resolver, sw.js written; URL normaliser, load-failure reload and worker registration injected into ${injected} page(s)`);
