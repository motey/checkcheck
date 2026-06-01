import { chromium } from "@playwright/test";
import { execSync } from "child_process";
import { spawn } from "child_process";
import { writeFile } from "fs/promises";
import { existsSync } from "fs";
import { resolve } from "path";

// Paths resolved relative to this file (CheckCheck/frontend/tests/e2e/)
const FRONTEND_ROOT = resolve(__dirname, "../..");
const BACKEND_DIR = resolve(FRONTEND_ROOT, "../backend");
const VENV_PYTHON = resolve(BACKEND_DIR, ".venv/bin/python");
const SERVER_SCRIPT = resolve(BACKEND_DIR, "tests/start_e2e_server.py");
const PID_FILE = resolve(__dirname, ".e2e-server.pid");
const E2E_BACKEND_PORT = 8182;

function killPortIfBusy(port: number): void {
  try {
    // fuser -k sends SIGKILL to any process holding the port
    execSync(`fuser -k ${port}/tcp`, { stdio: "ignore" });
    // Brief pause to let the kernel reclaim the port
    execSync("sleep 0.5");
  } catch {
    // No process was using the port — that's fine
  }
}

export default async function globalSetup(): Promise<void> {
  if (!existsSync(VENV_PYTHON)) {
    throw new Error(
      `Backend venv not found at ${VENV_PYTHON}.\n` +
        `Run: source build_server_dev_env.sh  (from the repo root)`
    );
  }

  // Clean up any orphaned process from a previous run before starting fresh.
  killPortIfBusy(E2E_BACKEND_PORT);

  console.log("▶  Starting E2E backend server …");

  // detached: true puts the child in its own process group so that
  // killing the group (process.kill(-pgid, 'SIGTERM')) also terminates
  // the forked uvicorn subprocess — not just the wrapper script.
  const proc = spawn(VENV_PYTHON, [SERVER_SCRIPT], {
    cwd: BACKEND_DIR,
    stdio: ["ignore", "pipe", "pipe"],
    detached: true,
  });

  // Forward backend stderr so startup errors are visible
  proc.stderr!.on("data", (d: Buffer) => process.stderr.write(d));

  await new Promise<void>((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error("E2E backend did not become ready within 60 s")),
      60_000
    );

    proc.stdout!.on("data", (d: Buffer) => {
      if (d.toString().includes("READY")) {
        clearTimeout(timeout);
        resolve();
      }
    });

    proc.on("error", (err) => {
      clearTimeout(timeout);
      reject(err);
    });

    proc.on("exit", (code) => {
      clearTimeout(timeout);
      if (code !== null && code !== 0) {
        reject(new Error(`Backend exited unexpectedly with code ${code}`));
      }
    });
  });

  // Save the process GROUP id (same as pid when detached:true starts a new group).
  await writeFile(PID_FILE, String(proc.pid));
  console.log(`✔  E2E backend ready on port ${E2E_BACKEND_PORT} (pid ${proc.pid})`);

  // The webServer command clears the Vite cache before every run, so Vite
  // always needs ~5 s to re-optimise on the first browser visit.  If a real
  // test connects before optimisation is done it gets a blank page.
  // Pre-warm here: load the login page and wait until the form renders.
  // The retry loop handles the brief window where Nitro is still building.
  console.log("▶  Warming up Nuxt dev server …");
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const warmupDeadline = Date.now() + 90_000;
  while (true) {
    try {
      await page.goto("http://localhost:3001/login", { waitUntil: "domcontentloaded" });
      // Wait for the form – this blocks until Vite's full optimisation cycle completes.
      await page.waitForSelector("form", { timeout: 10_000 });
      break;
    } catch {
      if (Date.now() > warmupDeadline) throw new Error("Nuxt did not become ready within 90 s");
      await new Promise((r) => setTimeout(r, 1_000));
    }
  }
  // Allow any secondary Vite rebuild triggered by loading the components to settle.
  await new Promise((r) => setTimeout(r, 2_000));
  await browser.close();
  console.log("✔  Nuxt dev server warmed up");
}
