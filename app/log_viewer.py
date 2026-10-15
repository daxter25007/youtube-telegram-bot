from __future__ import annotations

import json
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

LOG_FILE_RE = re.compile(r"bot\.log(?:\.\d+)?\Z")
LOG_DIR = Path(os.getenv("LOG_DIR", "/app/logs"))
MAX_LINES = 2_000

INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Журнал Telegram-бота</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #090d14; color: #dce7f5; }
    header { position: sticky; top: 0; z-index: 2; padding: 18px 22px; background: #101722;
      border-bottom: 1px solid #253247; box-shadow: 0 8px 30px #0008; }
    h1 { margin: 0 0 14px; font-size: 20px; font-weight: 650; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    input, select, button, label { min-height: 38px; border: 1px solid #31415a;
      border-radius: 9px; background: #151f2d; color: inherit; padding: 8px 11px; }
    input { flex: 1 1 260px; }
    button { cursor: pointer; }
    button:hover { border-color: #5d88c7; }
    label { display: inline-flex; align-items: center; gap: 7px; }
    label input { min-height: 0; flex: none; }
    main { padding: 18px 22px 28px; }
    .status { display: flex; gap: 8px; align-items: center; margin-bottom: 12px;
      color: #8fa2bc; font-size: 13px; }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: #f2b84b; }
    .dot.ok { background: #36d68b; box-shadow: 0 0 12px #36d68b88; }
    .dot.error { background: #ff6174; }
    pre { min-height: calc(100vh - 170px); margin: 0; padding: 16px; overflow: auto;
      white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid #253247;
      border-radius: 12px; background: #0c121c; color: #c9d7e8;
      font: 13px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace; }
    @media (max-width: 700px) { header, main { padding-left: 12px; padding-right: 12px; }
      select, button, label { flex: 1 1 auto; } }
  </style>
</head>
<body>
  <header>
    <h1>Журнал Telegram-бота</h1>
    <div class="toolbar">
      <select id="file" aria-label="Файл журнала"></select>
      <select id="lines" aria-label="Количество строк">
        <option value="100">100 строк</option>
        <option value="500" selected>500 строк</option>
        <option value="1000">1000 строк</option>
        <option value="2000">2000 строк</option>
      </select>
      <select id="level" aria-label="Уровень">
        <option value="">Все уровни</option>
        <option>INFO</option><option>WARNING</option><option>ERROR</option><option>CRITICAL</option>
      </select>
      <input id="search" type="search" placeholder="Поиск по журналу">
      <label><input id="autoscroll" type="checkbox" checked> Вниз</label>
      <button id="pause" type="button">Пауза</button>
      <button id="copy" type="button">Копировать</button>
    </div>
  </header>
  <main>
    <div class="status">
      <span id="dot" class="dot"></span><span id="status">Подключение…</span>
    </div>
    <pre id="output" aria-live="polite">Загрузка журнала…</pre>
  </main>
  <script>
    const state = { entries: [], paused: false, selectedFile: "bot.log" };
    const el = id => document.getElementById(id);

    function render() {
      const level = el("level").value;
      const search = el("search").value.toLowerCase();
      const shown = state.entries.filter(line => (!level || line.includes(` ${level} `)) &&
        (!search || line.toLowerCase().includes(search)));
      el("output").textContent =
        shown.join(String.fromCharCode(10)) || "Нет строк по выбранному фильтру.";
      if (el("autoscroll").checked) el("output").scrollTop = el("output").scrollHeight;
    }

    async function refresh() {
      if (state.paused) return;
      try {
        const params = new URLSearchParams({file: state.selectedFile, lines: el("lines").value});
        const response = await fetch(`/api/logs?${params}`, {cache: "no-store"});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        state.entries = data.lines;
        const selector = el("file");
        const previous = state.selectedFile;
        selector.replaceChildren(...data.files.map(name => new Option(name, name)));
        if (data.files.includes(previous)) selector.value = previous;
        else if (data.files.length) state.selectedFile = selector.value = data.files[0];
        el("dot").className = "dot ok";
        const updated = new Date().toLocaleTimeString();
        el("status").textContent = `${data.file} · ${data.lines.length} строк · ${updated}`;
        render();
      } catch (error) {
        el("dot").className = "dot error";
        el("status").textContent = `Ошибка обновления: ${error.message}`;
      }
    }

    el("file").addEventListener("change", event => {
      state.selectedFile = event.target.value;
      refresh();
    });
    el("lines").addEventListener("change", refresh);
    el("level").addEventListener("change", render);
    el("search").addEventListener("input", render);
    el("pause").addEventListener("click", () => {
      state.paused = !state.paused;
      el("pause").textContent = state.paused ? "Продолжить" : "Пауза";
      if (!state.paused) refresh();
    });
    el("copy").addEventListener("click", async () => {
      await navigator.clipboard.writeText(el("output").textContent);
      el("copy").textContent = "Скопировано";
      setTimeout(() => { el("copy").textContent = "Копировать"; }, 1200);
    });
    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""


def available_log_files(log_dir: Path) -> list[str]:
    try:
        names = [path.name for path in log_dir.iterdir() if LOG_FILE_RE.fullmatch(path.name)]
    except OSError:
        return []

    def sort_key(name: str) -> int:
        return 0 if name == "bot.log" else int(name.rsplit(".", 1)[1])

    return sorted(names, key=sort_key)


def read_log_tail(log_dir: Path, filename: str, line_count: int) -> list[str]:
    if not LOG_FILE_RE.fullmatch(filename):
        raise ValueError("Недопустимое имя файла")
    path = log_dir / filename
    limit = min(MAX_LINES, max(1, line_count))
    try:
        with path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            position = file.tell()
            chunks: list[bytes] = []
            newline_count = 0
            while position > 0 and newline_count <= limit:
                block_size = min(65_536, position)
                position -= block_size
                file.seek(position)
                chunk = file.read(block_size)
                chunks.append(chunk)
                newline_count += chunk.count(b"\n")
    except OSError:
        return []
    content = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
    return content.splitlines()[-limit:]


class LogViewerHandler(BaseHTTPRequestHandler):
    server_version = "BotLogViewer/1.0"

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        if request.path == "/":
            self._send(INDEX_HTML.encode(), "text/html; charset=utf-8")
            return
        if request.path == "/healthz":
            self._send(b"ok\n", "text/plain; charset=utf-8")
            return
        if request.path == "/api/logs":
            self._send_logs(parse_qs(request.query))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _send_logs(self, query: dict[str, list[str]]) -> None:
        files = available_log_files(LOG_DIR)
        filename = query.get("file", ["bot.log"])[0]
        if filename not in files and files:
            filename = files[0]
        try:
            line_count = int(query.get("lines", ["500"])[0])
            lines = read_log_tail(LOG_DIR, filename, line_count) if files else []
        except ValueError:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        payload = json.dumps(
            {"file": filename, "files": files, "lines": lines},
            ensure_ascii=False,
        ).encode()
        self._send(payload, "application/json; charset=utf-8")

    def _send(self, payload: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    host = os.getenv("LOG_VIEWER_HOST", "127.0.0.1")
    port = int(os.getenv("LOG_VIEWER_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), LogViewerHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
