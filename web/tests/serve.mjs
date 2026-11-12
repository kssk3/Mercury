import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { resolve, sep, extname } from "node:path";
const root = resolve("out");
const types = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".woff2": "font/woff2" };
createServer(async (request, response) => {
  try {
    let file = resolve(root, "." + decodeURIComponent(new URL(request.url, "http://localhost").pathname));
    if (file !== root && !file.startsWith(root + sep)) throw new Error("Invalid path");
    if ((await stat(file)).isDirectory()) file = resolve(file, "index.html");
    response.writeHead(200, { "Content-Type": types[extname(file)] ?? "application/octet-stream" });
    response.end(await readFile(file));
  } catch {
    response.writeHead(404); response.end("Not found");
  }
}).listen(4173, "127.0.0.1");
