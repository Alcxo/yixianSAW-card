/* 出卡引擎 Worker：把 Python（Pyodide）+ 现有 swatch 算法跑在后台线程里 */
import { loadPyodide } from "./vendor/pyodide.mjs";

const PY_FILES = [
  "py/bridge.py",
  "py/swatch/__init__.py",
  "py/swatch/card.py",
  "py/swatch/imageio.py",
  "py/swatch/lace.py",
  "py/swatch/layout.py",
  "py/swatch/paths.py",
  "py/swatch/table.py",
  "py/swatch/template.py",
  "py/swatch/upscale.py",
];

let pyodide = null;
let booting = null;
let stage = "准备";

function post(msg, transfer) {
  self.postMessage(msg, transfer || []);
}

function say(stageName, text) {
  stage = stageName;
  post({ type: "boot", stage: stageName, text });
}

async function head(url) {
  try {
    const res = await fetch(url, { method: "HEAD" });
    return { ok: res.ok, status: res.status, size: Number(res.headers.get("content-length") || 0) };
  } catch (err) {
    return { ok: false, status: 0, size: 0, error: String(err && err.message || err) };
  }
}

// 先探一下几个大文件是不是真的在、是不是被截断了（部署漏文件是最常见的故障）
async function preflight() {
  const must = [
    "vendor/pyodide.asm.wasm",
    "vendor/python_stdlib.zip",
    "vendor/pyodide-lock.json",
    "py/bridge.py",
  ];
  const missing = [];
  for (const path of must) {
    const info = await head(new URL(path, self.location.href).href);
    if (info.status === 404 || info.status === 410) {
      missing.push(`${path}（HTTP ${info.status}，文件没传上去）`);
    } else if (path.endsWith(".wasm") && info.size && info.size < 1000000) {
      missing.push(`${path} 只有 ${info.size} 字节，像是没传完整`);
    }
  }
  if (missing.length) {
    throw Object.assign(new Error("引擎文件缺失或被截断：\n" + missing.join("\n")), { stage: "文件检查" });
  }
}

async function writeSources() {
  for (const path of PY_FILES) {
    const res = await fetch(path, { cache: "force-cache" });
    if (!res.ok) throw new Error("缺少引擎文件：" + path);
    const bytes = new Uint8Array(await res.arrayBuffer());
    const target = "/app/" + path.replace(/^py\//, "");
    pyodide.FS.writeFile(target, bytes);
  }
}

async function boot() {
  if (pyodide) return pyodide;
  if (booting) return booting;
  booting = (async () => {
    say("准备", "正在检查引擎文件…");
    await preflight();
    say("core", "正在加载 Python 内核（首次约 10 秒）…");
    pyodide = await loadPyodide({
      indexURL: new URL("./vendor/", self.location.href).href,
    });
    say("packages", "正在加载 numpy / Pillow…");
    await pyodide.loadPackage(["numpy", "pillow", "xlrd"], {
      messageCallback: (m) => {
        if (/Loading|Loaded/.test(m)) say("packages", m.replace(/^.*?:\s*/, ""));
      },
    });
    say("sources", "正在装载出卡算法…");
    pyodide.FS.mkdirTree("/app/swatch");
    await writeSources();
    pyodide.runPython(
      "import sys; sys.path.insert(0, '/app'); " +
      "from bridge import init, detect, set_table, lookup, render_one",
    );
    pyodide.FS.mkdirTree("/work/in");
    pyodide.FS.mkdirTree("/work/out");
    post({ type: "ready", version: pyodide.version });
  })();
  return booting;
}

async function handle(msg) {
  await boot();
  const py = pyodide;

  if (msg.type === "init") {
    py.FS.writeFile("/app/template.jpg", new Uint8Array(msg.template));
    py.FS.writeFile("/app/template.json", new Uint8Array(msg.profile));
    const info = py.globals.get("init")("/app/template.jpg", "/app/template.json");
    post({ type: "init-ok", info: JSON.parse(info) });
    return;
  }

  if (msg.type === "template") {
    py.FS.writeFile("/work/template_new.bin", new Uint8Array(msg.bytes));
    const info = py.globals.get("detect")("/work/template_new.bin", "/work/template_new.json");
    const profile = py.FS.readFile("/work/template_new.json");
    const init = py.globals.get("init")("/work/template_new.bin", "/work/template_new.json");
    post({
      type: "template-ok",
      info: JSON.parse(info),
      profile: profile.buffer,
      template: msg.bytes,
    }, [profile.buffer]);
    return;
  }

  if (msg.type === "table") {
    if (!msg.bytes) {
      py.globals.get("set_table")("");
      post({ type: "table-ok", name: "" });
      return;
    }
    const ext = (msg.name.split(".").pop() || "csv").toLowerCase();
    const path = "/work/table." + ext;
    py.FS.writeFile(path, new Uint8Array(msg.bytes));
    py.globals.get("set_table")(path);
    post({ type: "table-ok", name: msg.name });
    return;
  }

  if (msg.type === "render") {
    const params = JSON.stringify(msg.params || {});
    const total = msg.jobs.length;
    for (let i = 0; i < total; i += 1) {
      const job = msg.jobs[i];
      const inPath = `/work/in/${job.id}.jpg`;
      const outPath = `/work/out/${job.id}.jpg`;
      try {
        py.FS.writeFile(inPath, new Uint8Array(job.bytes));
        const info = JSON.parse(py.globals.get("render_one")(
          inPath, outPath, job.model || "", params,
        ));
        const bytes = py.FS.readFile(outPath);
        post({
          type: "card", id: job.id, index: i + 1, total, info,
          bytes: bytes.buffer,
        }, [bytes.buffer]);
      } catch (err) {
        post({ type: "card-error", id: job.id, index: i + 1, total, message: String(err && err.message || err) });
      } finally {
        try { py.FS.unlink(inPath); } catch (e) { /* ignore */ }
        try { py.FS.unlink(outPath); } catch (e) { /* ignore */ }
      }
    }
    post({ type: "render-done" });
    return;
  }
}

self.onmessage = async (ev) => {
  const msg = ev.data || {};
  if (msg.type === "boot") {
    try { await boot(); post({ type: "ready" }); } catch (err) {
      post({ type: "fatal", message: String(err && err.message || err) });
    }
    return;
  }
  try {
    await handle(msg);
  } catch (err) {
    post({
      type: "fatal",
      stage: err && err.stage ? err.stage : stage,
      message: String(err && err.message || err),
    });
  }
};
