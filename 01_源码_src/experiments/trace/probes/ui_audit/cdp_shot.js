// Minimal CDP screenshot harness (no npm deps; Node 24 global WebSocket/fetch).
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const [url, outPng, wStr, hStr, cookieFile, evalScript] = process.argv.slice(2);
const W = parseInt(wStr || "1280", 10), H = parseInt(hStr || "900", 10);
const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const PORT = 9333 + Math.floor(Math.random() * 300);
const profile = path.join(require("os").tmpdir(), "cdpshot-" + Date.now());

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function launch() {
  const headful = process.env.HEADFUL === "1";
  const args = (headful ? [] : ["--headless=new"]).concat(["--disable-gpu", "--hide-scrollbars", "--no-first-run",
    "--no-default-browser-check", "--disable-extensions", "--mute-audio",
    "--remote-debugging-port=" + PORT, "--user-data-dir=" + profile,
    "--window-size=" + W + "," + H, "about:blank"]);
  return spawn(CHROME, args, { stdio: "ignore", windowsHide: true });
}

async function wsUrl() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch("http://127.0.0.1:" + PORT + "/json/version");
      const j = await r.json();
      if (j.webSocketDebuggerUrl) return j.webSocketDebuggerUrl;
    } catch (_) {}
    await sleep(200);
  }
  throw new Error("devtools endpoint never came up");
}

class Cdp {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.listeners = []; 
    ws.addEventListener("message", (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) { const { res, rej } = this.pending.get(m.id); this.pending.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); }
      else if (m.method) this.listeners.forEach(fn => fn(m));
    });
  }
  send(method, params, sessionId) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      this.ws.send(JSON.stringify({ id, method, params: params || {}, sessionId }));
    });
  }
  once(method, timeoutMs) {
    return new Promise((res, rej) => {
      const t = setTimeout(() => rej(new Error("timeout waiting " + method)), timeoutMs || 20000);
      const fn = (m) => { if (m.method === method) { clearTimeout(t); this.listeners = this.listeners.filter(x => x !== fn); res(m.params); } };
      this.listeners.push(fn);
    });
  }
}

(async () => {
  const chrome = launch();
  let code = 0;
  try {
    const ws = new WebSocket(await wsUrl());
    await new Promise((res, rej) => { ws.addEventListener("open", res); ws.addEventListener("error", rej); });
    const cdp = new Cdp(ws);
    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
    const S = sessionId;
    await cdp.send("Page.enable", {}, S);
    await cdp.send("Runtime.enable", {}, S);
    cdp.listeners.push((m) => {
      if (m.method === "Runtime.exceptionThrown") {
        const d = m.params && m.params.exceptionDetails || {};
        console.error("PAGE-EXCEPTION:", (d.exception && (d.exception.description || d.exception.value)) || d.text);
      } else if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
        console.error("PAGE-CONSOLE:", (m.params.args || []).map(a => a.value || a.description || "").join(" ").slice(0, 300));
      }
    });
    await cdp.send("Network.enable", {}, S);
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 2, mobile: W < 600 }, S);
    if (cookieFile && fs.existsSync(cookieFile)) {
      const cookies = JSON.parse(fs.readFileSync(cookieFile, "utf8"));
      for (const c of cookies) {
        await cdp.send("Network.setCookie", {
          name: c.name, value: c.value, domain: c.domain || "127.0.0.1", path: c.path || "/",
          httpOnly: !!c.httpOnly, secure: !!c.secure
        }, S);
      }
    }
    const loaded = cdp.once("Page.loadEventFired", 45000);
    await cdp.send("Page.navigate", { url }, S);
    await loaded;
    await sleep(1200);
    let expr = evalScript || "";
    if (expr.startsWith("@file:")) expr = fs.readFileSync(expr.slice(6), "utf8").trim();
    if (expr) {
      const parts = expr.split("__RELOAD__");
      const run = async (expr) => {
        const r = await cdp.send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }, S);
        if (r.exceptionDetails) console.error("EVAL ERROR:", JSON.stringify(r.exceptionDetails).slice(0, 400));
        else if (r.result && r.result.value !== undefined) console.log("EVAL:", JSON.stringify(r.result.value).slice(0, 800));
      };
      await run(parts[0]);
      if (parts.length > 1) {
        const again = cdp.once("Page.loadEventFired", 45000);
        await cdp.send("Page.reload", { ignoreCache: true }, S);
        await again;
        await sleep(2200);
    // 可选：PREJS="@file:path" 在 reload 之后、CLICK 之前注入脚本（装监听器/埋点）
    if (process.env.PREJS) {
      let psrc = process.env.PREJS;
      if (psrc.startsWith("@file:")) psrc = fs.readFileSync(psrc.slice(6), "utf8");
      const pr = await cdp.send("Runtime.evaluate", { expression: psrc, awaitPromise: true, returnByValue: true }, S);
      if (pr.exceptionDetails) console.error("PREJS ERROR:", JSON.stringify(pr.exceptionDetails).slice(0, 300));
      else console.log("PREJS:", JSON.stringify(pr.result && pr.result.value).slice(0, 400));
    }
    // 可选：CLICK="x,y" 用真实鼠标事件点击（验证按钮命中区与冒泡/阻止冒泡）
    if (process.env.CLICK) {
      const [cx, cy] = process.env.CLICK.split(",").map(Number);
      await sleep(parseInt(process.env.CLICK_DELAY || "0", 10));
      if (process.env.CLICK_JIGGLE) {
        const [jx, jy] = process.env.CLICK_JIGGLE.split(",").map(Number);
        await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: jx, y: jy, buttons: 0 }, S);
        await sleep(80);
      }
      const mouse = (type, x, y, buttons) => cdp.send("Input.dispatchMouseEvent",
        { type, x, y, button: "left", buttons, clickCount: 1 }, S);
      await mouse("mouseMoved", cx, cy, 0);
      await mouse("mousePressed", cx, cy, 1);
      await sleep(60);
      await mouse("mouseReleased", cx, cy, 0);
      await sleep(350);
      console.log("CLICK done");
    }
    // 可选：DRAG="x1,y1,x2,y2[,steps]" 用真实鼠标事件做拖拽（合成 PointerEvent 无法验证指针捕获）
    if (process.env.DRAG) {
      const [x1, y1, x2, y2, stepsRaw] = process.env.DRAG.split(",").map(Number);
      const steps = stepsRaw || 12;
      const mouse = (type, x, y, extra) => cdp.send("Input.dispatchMouseEvent",
        Object.assign({ type, x, y, button: "left", buttons: type === "mouseReleased" ? 0 : 1, clickCount: 1 }, extra || {}), S);
      await mouse("mouseMoved", x1, y1);
      await mouse("mousePressed", x1, y1);
      for (let i = 1; i <= steps; i++) {
        await mouse("mouseMoved", x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps);
        await sleep(16);
      }
      await mouse("mouseReleased", x2, y2);
      await sleep(300);
      console.log("DRAG done");
    }
        await run(parts[1]);
      }
      await sleep(1500);
    }
    const shot = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, S);
    fs.writeFileSync(outPng, Buffer.from(shot.data, "base64"));
    console.log("OK", outPng, fs.statSync(outPng).size, "bytes");
    ws.close();
  } catch (e) {
    console.error("FAIL:", e.message); code = 1;
  } finally {
    try { chrome.kill(); } catch (_) {}
    await sleep(300);
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (_) {}
  }
  process.exit(code);
})();


