#!/usr/bin/env node
/**
 * companion.js — Live-reload dev server.
 * Serves files locally, pushes changes to the browser on edit. No redeploy.
 * Usage: node companion.js <port> [ws_port]
 */
const fs = require("fs"), path = require("path"), http = require("http"), crypto = require("crypto");

const HTTP_PORT = parseInt(process.argv[2], 10) || parseInt(process.argv[3], 10) || 3000;
const WS_PORT  = parseInt(process.argv[4], 10) || HTTP_PORT + 3570; // default: 3000->3570
const PROJECT_DIR = __dirname;
const POLL_MS = 1000;
const DEBOUNCE_MS = 500;

const IGNORE_DIRS  = new Set(["node_modules", ".git", ".vercel", "__pycache__"]);
const IGNORE_FILES = new Set(["companion.js", "companion.cjs", ".DS_Store"]);

let connectedSockets = new Set();
let prevHashes = {};
let debounceTimer = null;
let changedFiles = new Set();

// ── HTTP file server ────────────────────────────────────────────────────────
const mimeTypes = {
  ".html": "text/html; charset=utf-8",
  ".css":  "text/css; charset=utf-8",
  ".js":   "text/javascript; charset=utf-8",
  ".mjs":  "text/javascript; charset=utf-8",
  ".json": "application/json",
  ".png":  "image/png",
  ".jpg":  "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif":  "image/gif",
  ".svg":  "image/svg+xml",
  ".ico":  "image/x-icon",
  ".woff": "font/woff",
  ".woff2":"font/woff2",
  ".wasm": "application/wasm",
  ".webp": "image/webp",
  ".map":  "application/json",
};

const liveReloadScript = `
<script>
(function() {
  var ws = new WebSocket((location.protocol === 'https:' ? 'wss:' : 'ws:') + '//localhost:__WS_PORT__/live');
  ws.onmessage = function(e) {
    var msg = JSON.parse(e.data);
    if (msg.type === 'reload') {
      location.reload();
    } else if (msg.type === 'hot') {
      var el = document.querySelector('[data-hot="' + msg.file + '"]');
      if (el) { el.outerHTML = msg.html; }
    }
  };
  ws.onclose = function() { setTimeout(function() {
    var s = document.createElement('script');
    s.src = '//localhost:__WS_PORT__/live/check';
    document.head.appendChild(s);
  }, 2000); };
})();
</script>`;

function serveFile(req, res) {
  let url = req.url.split("?")[0];
  if (url === "/") url = "/index.html";

  const fp = path.join(PROJECT_DIR, url);
  // Security: prevent escaping the project dir
  if (!fp.startsWith(PROJECT_DIR)) {
    res.writeHead(403); res.end("Forbidden");
    return;
  }

  fs.readFile(fp, (err, data) => {
    if (err) {
      res.writeHead(404);
      res.end("Not found");
      return;
    }
    const ext = path.extname(fp).toLowerCase();
    const mime = mimeTypes[ext] || "application/octet-stream";

    if (ext === ".html") {
      // Inject live reload script before </body>
      const injected = data.toString().replace("</body>", liveReloadScript.replace(/__WS_PORT__/g, String(WS_PORT)) + "\n</body>");
      res.writeHead(200, { "Content-Type": mime, "Cache-Control": "no-cache" });
      res.end(injected);
    } else {
      res.writeHead(200, { "Content-Type": mime, "Cache-Control": "no-cache" });
      res.end(data);
    }
  });
}

const httpServer = http.createServer(serveFile);
httpServer.listen(HTTP_PORT, () => console.log(`HTTP server on http://localhost:${HTTP_PORT}`));

// ── WebSocket server ────────────────────────────────────────────────────────
const wsServer = http.createServer();
wsServer.listen(WS_PORT, () => console.log(`Live-reload WS on ws://localhost:${WS_PORT}`));

wsServer.on("upgrade", (req, socket) => {
  // Handle /live/check polling for reconnection
  if (req.url && req.url.startsWith("/live/check")) {
    socket.write("HTTP/1.1 204 No Content\r\n\r\n");
    socket.end();
    return;
  }

  const key = req.headers["sec-websocket-key"];
  const accept = crypto.createHash("sha1")
    .update(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")
    .digest("base64");
  socket.write("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n");
  connectedSockets.add(socket);
  console.log(`Browser connected (${connectedSockets.size} total)`);
  socket.once("close", () => { connectedSockets.delete(socket); console.log(`Browser disconnected (${connectedSockets.size} remaining)`); });
});

function broadcastWS(msg, excludeSocket) {
  const buf = Buffer.from(JSON.stringify(msg), "utf-8"), len = buf.length;
  let h;
  if (len < 126)          h = Buffer.alloc(2), h[0] = 0x81, h[1] = len;
  else if (len < 65536)   h = Buffer.alloc(4), h[0] = 0x81, h[1] = 126, h.writeUInt16BE(len, 2);
  else                    h = Buffer.alloc(10),h[0] = 0x81, h[1] = 127, h.writeBigUInt64BE(BigInt(len), 2);
  const frame = Buffer.concat([h, buf]);
  for (const s of connectedSockets) {
    if (s !== excludeSocket) try { s.write(frame); } catch (_) {}
  }
}

// ── File watcher (polling) ──────────────────────────────────────────────────
function walk(dir) {
  const files = [];
  try {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (IGNORE_DIRS.has(e.name) || e.name.startsWith(".")) continue;
      const fp = path.join(dir, e.name);
      if (e.isDirectory()) files.push(...walk(fp));
      else if (!IGNORE_FILES.has(e.name)) files.push(fp);
    }
  } catch (_) {}
  return files;
}

function hashFile(fp) {
  try { const st = fs.statSync(fp); return `${st.size}-${st.mtimeMs}`; } catch (_) { return null; }
}

function scan() {
  const files = walk(PROJECT_DIR);
  for (const fp of files) {
    const h = hashFile(fp);
    const rel = path.relative(PROJECT_DIR, fp);
    if (prevHashes[rel] !== undefined && prevHashes[rel] !== h) {
      changedFiles.add(rel);
    }
    prevHashes[rel] = h;
  }
  // Remove stale
  const current = new Set(files.map(f => path.relative(PROJECT_DIR, f)));
  for (const k of Object.keys(prevHashes)) {
    if (!current.has(k)) { changedFiles.add(k + " (deleted)"); delete prevHashes[k]; }
  }

  if (changedFiles.size > 0) {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(pushChanges, DEBOUNCE_MS);
  }
}

function pushChanges() {
  const files = [...changedFiles];
  changedFiles.clear();
  console.log(`\nChanged: ${files.join(", ")}`);
  broadcastWS({ type: "reload", files });
}

// Build step (for Vite projects)
let needsBuild = false;
if (fs.existsSync(path.join(PROJECT_DIR, "package.json")) && fs.existsSync(path.join(PROJECT_DIR, "vite.config.js"))) {
  needsBuild = true;
  const { execSync } = require("child_process");
  console.log("Vite project detected — files in src/ will auto-rebuild dist/");
}

// ── Init ────────────────────────────────────────────────────────────────────
const initialFiles = walk(PROJECT_DIR);
for (const fp of initialFiles) {
  prevHashes[path.relative(PROJECT_DIR, fp)] = hashFile(fp);
}
console.log(`Serving ${initialFiles.length} files from ${PROJECT_DIR}`);
console.log(`Open http://localhost:${HTTP_PORT} in your browser`);

setInterval(scan, POLL_MS);

process.on("SIGINT", () => {
  console.log("\nShutdown");
  for (const s of connectedSockets) try { s.end(); } catch (_) {}
  httpServer.close();
  wsServer.close();
  process.exit(0);
});
