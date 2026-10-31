const fs = require("fs"), path = require("path"), { spawnSync } = require("child_process");
const [probeFile, outPng] = process.argv.slice(2);
const W = process.env.W || "430", H = process.env.H || "900";
const expr = fs.readFileSync(probeFile, "utf8");
const r = spawnSync(process.execPath, [path.join(__dirname, "cdp_shot.js"),
  "http://127.0.0.1:8010/", outPng || path.join(__dirname, "sized.png"),
  W, H, path.join(__dirname, "cookie.json"), expr], { encoding: "utf8" });
process.stdout.write(r.stdout || ""); process.stderr.write(r.stderr || "");
