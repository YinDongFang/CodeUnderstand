#!/usr/bin/env node
/**
 * Single-file port of download.cmd + download_run.ps1 + download_cmd_agent.ps1
 *
 * Usage: node download.js <GitHub ZIP URL>
 *   Example: node download.js https://github.com/org/repo/archive/refs/heads/main.zip
 *
 * CWD: projects/, questions/ and git operations use process.cwd().
 * Prompt: 提示词.md next to this file (__dirname); {repo} is replaced.
 * Download: https://gh-proxy.org/<url>  (same as download.sh)
 * Skips: zip exists -> skip download; extracted dir exists -> skip extract;
 *        final projects/<repo> exists -> skip download+extract+rename.
 * Extract: WINRAR env, WinRAR.exe in Program Files, registry exe64; else tar; else PowerShell Expand-Archive.
 * Agent: `agent --trust -p <prompt>` with cwd `projects/<repo>`.
 *   Prompt is sent as **one argv line**: CR/LF → literal two-char `\` + `n` (so shells do not break multiline).
 *   Spawn **without** `shell: true` (direct argv; avoids cmd truncation).
 * On agent failure: no questions/<repo>.txt, no git; see questions/<repo>.agent-log.txt
 *
 * Requires: Node 18+ (fetch), git; optional curl (else fetch only), WinRAR or tar or PowerShell.
 * All I/O and subprocess calls are async (Promises).
 */

"use strict";

const fs = require("fs");
const fsp = require("fs/promises");
const path = require("path");
const { spawn } = require("child_process");

const PROMPT_FILE = "\u63d0\u793a\u8bcd.md"; // 提示词.md
const ZIP_URL_RE =
  /^https:\/\/github\.com\/([^/]+)\/([^/]+)\/archive\/refs\/heads\/([^.]+)\.zip$/;

const COMMIT_PREFIX = Buffer.from([
  0xe8, 0x87, 0xaa, 0xe5, 0x8a, 0xa8, 0xe5, 0x88, 0x9b, 0xe5, 0xbb, 0xba, 0xe9,
  0xa2, 0x98, 0xe7, 0x9b, 0xae, 0x20,
]).toString("utf8"); // "自动创建题目 "

const MAX_AGENT_CAPTURE = 100 * 1024 * 1024;

/**
 * Turn multiline prompt into a single physical line: each newline becomes literal `\` + `n`
 * (backslash-n), for argv / shell compatibility. Normalize CRLF/CR first.
 * @param {string} text
 */
function promptToSingleLine(text) {
  return text
    .replace(/\r\n/g, "\n\n").replace(/"/g, "'");
}

function log(...args) {
  console.log("[download]", ...args);
}

function logErr(...args) {
  console.error("[download]", ...args);
}

/** @param {string} p */
async function pathExists(p) {
  try {
    await fsp.access(p);
    return true;
  } catch {
    return false;
  }
}

/** @param {string} p */
async function safeUnlink(p) {
  try {
    await fsp.unlink(p);
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
  }
}

/** @param {string} dir */
async function safeRmrf(dir) {
  await fsp.rm(dir, { recursive: true, force: true });
}

/**
 * @param {string} cmd
 * @param {string[]} args
 * @param {import('child_process').SpawnOptions} [opts]
 * @returns {Promise<{ code: number | null, signal: NodeJS.Signals | null, stdout: string, stderr: string }>}
 */
function spawnCapture(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      windowsHide: true,
      ...opts,
    });
    /** @type {Buffer[]} */
    const out = [];
    /** @type {Buffer[]} */
    const err = [];

    const push = (chunks, buf, lenRef) => {
      if (lenRef.v + buf.length > MAX_AGENT_CAPTURE) {
        chunks.push(buf.subarray(0, Math.max(0, MAX_AGENT_CAPTURE - lenRef.v)));
        lenRef.v = MAX_AGENT_CAPTURE;
      } else {
        chunks.push(buf);
        lenRef.v += buf.length;
      }
    };

    const oLen = { v: 0 };
    const eLen = { v: 0 };

    child.stdout?.on("data", (d) => push(out, d, oLen));
    child.stderr?.on("data", (d) => push(err, d, eLen));

    child.on("error", reject);
    child.on("close", (code, signal) => {
      resolve({
        code,
        signal,
        stdout: Buffer.concat(out).toString("utf8"),
        stderr: Buffer.concat(err).toString("utf8"),
      });
    });
  });
}

/**
 * @param {string} cmd
 * @param {string[]} args
 * @param {import('child_process').SpawnOptions} [opts]
 * @returns {Promise<{ code: number | null, signal: NodeJS.Signals | null }>}
 */
function spawnInherit(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      stdio: "inherit",
      windowsHide: true,
      ...opts,
    });
    child.on("error", reject);
    child.on("close", (code, signal) => resolve({ code, signal }));
  });
}

async function findWinRar() {
  if (process.env.WINRAR && (await pathExists(process.env.WINRAR))) {
    return process.env.WINRAR;
  }
  const pf = process.env.ProgramFiles || "C:\\Program Files";
  const pf86 = process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)";
  for (const p of [
    path.join(pf, "WinRAR", "WinRAR.exe"),
    path.join(pf86, "WinRAR", "WinRAR.exe"),
  ]) {
    if (await pathExists(p)) return p;
  }
  for (const regKey of [
    "HKLM\\SOFTWARE\\WinRAR",
    "HKLM\\SOFTWARE\\WOW6432Node\\WinRAR",
  ]) {
    const r = await spawnCapture("reg", ["query", regKey, "/v", "exe64"], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    if (r.code !== 0 || !r.stdout) continue;
    const line = r.stdout.split(/\r?\n/).find((l) => l.includes("exe64"));
    if (!line) continue;
    const m = line.match(/REG_SZ\s+(.+)$/);
    if (m) {
      const exe = m[1].trim();
      if (exe && (await pathExists(exe))) return exe;
    }
  }
  return null;
}

/**
 * @param {string} zipFile
 * @param {string} destDir
 * @param {string | null} winrar
 */
async function expandArchive(zipFile, destDir, winrar) {
  const destSep = destDir.endsWith(path.sep) ? destDir : destDir + path.sep;
  if (winrar) {
    log("extract with WinRAR:", winrar);
    const { code } = await spawnInherit(winrar, [
      "x",
      "-y",
      "-o+",
      zipFile,
      destSep,
    ]);
    if (code === null || code >= 2) {
      throw new Error(`WinRAR exit ${code}`);
    }
    return;
  }
  log("extract with tar (or PowerShell fallback)");
  const tr = await spawnInherit("tar", ["-xf", zipFile, "-C", destDir]);
  if (tr.code === 0) return;

  const ps = [
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    `Expand-Archive -LiteralPath '${zipFile.replace(/'/g, "''")}' -DestinationPath '${destDir.replace(/'/g, "''")}' -Force`,
  ];
  const pr = await spawnInherit("powershell.exe", ps);
  if (pr.code !== 0) {
    throw new Error(`Expand-Archive failed, exit ${pr.code}`);
  }
}

async function curlAvailable() {
  const r = await spawnCapture("curl", ["--version"], {
    stdio: ["ignore", "pipe", "pipe"],
  });
  return r.code === 0;
}

/**
 * @param {string} proxyUrl
 * @param {string} destPath
 */
async function downloadZip(proxyUrl, destPath) {
  if (await curlAvailable()) {
    log("downloading with curl ->", destPath);
    const { code } = await spawnInherit("curl", [
      "-fSL",
      "--connect-timeout",
      "60",
      "--max-time",
      "0",
      "-o",
      destPath,
      proxyUrl,
    ]);
    if (code !== 0) throw new Error(`curl exit ${code}`);
    return;
  }
  log("downloading with fetch ->", destPath);
  const res = await fetch(proxyUrl, { redirect: "follow" });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
  const buf = Buffer.from(await res.arrayBuffer());
  await fsp.writeFile(destPath, buf);
}

/**
 * @param {string} workDir
 * @param {string} prompt
 * @param {string} outFile
 */
async function runAgent(workDir, prompt, outFile) {
  const promptArg = promptToSingleLine(prompt);
  log("=== agent step start ===");
  log(`WorkDir=${workDir}`);
  log(`OutFile=${outFile}`);
  log(`Prompt:\n\n${promptArg}`);

  log(
    `prompt: rawLen=${prompt.length} flatLen=${promptArg.length} (each newline -> two chars backslash + n)`,
  );

  const args = ["--trust", "-p", promptArg];
  let r;
  try {
    r = await spawnCapture("agent", args, {
      cwd: workDir,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
      shell: true,
    });
  } catch (e) {
    log(`EXCEPTION: ${/** @type {Error} */ (e).message}`);
    await safeUnlink(outFile);
    return { ok: false, code: 127 };
  }

  const code = r.code === null ? 1 : r.code;
  const textOut = [r.stdout || "", r.stderr || ""].filter(Boolean).join("\n");

  if (code !== 0) {
    log(`FAILED ExitCode=${code}`);
    if (textOut.trim()) {
      log("---- process output ----");
      const max = 8000;
      const clip =
        textOut.length > max
          ? textOut.slice(0, max) + "\n... (truncated)"
          : textOut;
      for (const ln of clip.split(/\r\n|\n|\r/)) {
        if (ln.length) log(ln);
      }
    } else {
      log("(no stdout/stderr captured)");
    }
    await safeUnlink(outFile);
    return { ok: false, code };
  }

  if (!textOut.trim()) {
    log("FAILED: empty stdout/stderr with ExitCode=0 (treated as failure)");
    await safeUnlink(outFile);
    return { ok: false, code: 1 };
  }

  await fsp.writeFile(outFile, textOut, "utf8");
  log("ExitCode=0 OK, wrote OutFile");
  return { ok: true, code: 0 };
}

/**
 * @param {string} rootCd
 * @param {string} outRel
 * @param {string} agentLogRel
 * @param {string} repoName
 */
async function runGit(rootCd, outRel, agentLogRel, repoName) {
  const git = async (args) => {
    const r = await spawnInherit("git", args, { cwd: rootCd });
    return r.code;
  };

  if ((await git(["add", "--", outRel])) !== 0) {
    throw exitError(1, "git add failed");
  }
  if (agentLogRel && (await pathExists(path.join(rootCd, agentLogRel)))) {
    if ((await git(["add", "--", agentLogRel])) !== 0) {
      throw exitError(1, "git add agent-log failed");
    }
  }
  const st = await git(["commit", "-m", COMMIT_PREFIX + repoName]);
  if (st !== 0) {
    logErr("git commit failed (nothing to commit? user.name/email?)");
    throw exitError(1, "git commit failed");
  }
  if ((await git(["push"])) !== 0) {
    logErr("git push failed");
    throw exitError(1, "git push failed");
  }
}

function exitError(code, message) {
  /** @type {Error & { exitCode?: number }} */
  const e = /** @type {any} */ (new Error(message));
  e.exitCode = code;
  return e;
}

async function main() {
  const zipUrl = process.argv[2];
  if (!zipUrl) {
    logErr("Usage: node download.js <GitHub ZIP URL>");
    logErr(
      "Example: node download.js https://github.com/org/repo/archive/refs/heads/main.zip",
    );
    throw exitError(1, "bad usage");
  }

  const m = zipUrl.match(ZIP_URL_RE);
  if (!m) {
    logErr(
      "Invalid GitHub ZIP URL. Expected: https://github.com/<user>/<repo>/archive/refs/heads/<branch>.zip",
    );
    throw exitError(1, "bad zip url");
  }
  const ghUser = m[1];
  const repo = m[2];
  const branch = m[3];

  const scriptRoot = __dirname;
  const promptPath = path.join(scriptRoot, PROMPT_FILE);
  if (!(await pathExists(promptPath))) {
    logErr("Prompt file not found:", promptPath);
    throw exitError(1, "no prompt file");
  }

  const promptText = (await fsp.readFile(promptPath, "utf8")).replace(
    /\{repo\}/g,
    repo,
  );

  const rootCd = process.cwd();
  const projectsDir = path.join(rootCd, "projects");
  const questionsDir = path.join(rootCd, "questions");
  await fsp.mkdir(projectsDir, { recursive: true });
  await fsp.mkdir(questionsDir, { recursive: true });

  const zipFile = path.join(projectsDir, `${repo}.zip`);
  const extracted = path.join(projectsDir, `${repo}-${branch}`);
  const target = path.join(projectsDir, repo);
  const winrar = await findWinRar();

  log(`${ghUser}/${repo} @ ${branch}`);

  if (await pathExists(target)) {
    log("skip download + extract: directory exists:", target);
  } else {
    if (!(await pathExists(zipFile))) {
      const proxyUrl = `https://gh-proxy.org/${zipUrl}`;
      await downloadZip(proxyUrl, zipFile);
    } else {
      log("skip download: zip exists:", zipFile);
    }

    if (!(await pathExists(zipFile))) {
      logErr("zip file missing");
      throw exitError(1, "zip missing");
    }

    if (await pathExists(extracted)) {
      log("skip extract: folder exists:", extracted);
    } else {
      await expandArchive(zipFile, projectsDir, winrar);
    }

    if (!(await pathExists(extracted))) {
      logErr("Extracted folder not found:", extracted);
      throw exitError(1, "extract missing");
    }

    log("rename ->", target);
    if (await pathExists(target)) await safeRmrf(target);
    await fsp.rename(extracted, target);
  }

  const outFile = path.join(questionsDir, `${repo}.txt`);
  const outRel = path.join("questions", `${repo}.txt`);
  const agentLogRel = path.join("questions", `${repo}.agent-log.txt`);

  log("agent ->", outFile);
  const ar = await runAgent(__dirname, promptText, outFile);
  if (!ar.ok) {
    logErr(
      `agent failed (exit ${ar.code}). No questions file; git skipped. Log: ${agentLogRel}`,
    );
    await safeUnlink(outFile);
    throw exitError(ar.code, "agent failed");
  }

  await runGit(rootCd, outRel, agentLogRel, repo);
  log("done");
}

main()
  .then(() => {
    process.exit(0);
  })
  .catch((err) => {
    logErr(err.message || err);
    const code = typeof err.exitCode === "number" ? err.exitCode : 1;
    process.exit(code);
  });
