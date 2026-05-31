import { readFile, unlink, access } from "fs/promises";
import { resolve } from "path";

const PID_FILE = resolve(__dirname, ".e2e-server.pid");

export default async function globalTeardown(): Promise<void> {
  try {
    await access(PID_FILE);
  } catch {
    return;
  }

  const pid = parseInt(await readFile(PID_FILE, "utf8"), 10);

  try {
    // Kill the entire process GROUP (negative pid) so the forked uvicorn
    // child is also terminated, not just the wrapper script.
    process.kill(-pid, "SIGTERM");
    console.log(`✔  E2E backend stopped (process group ${pid})`);
  } catch {
    // Process group may have already exited — nothing to do
  }

  await unlink(PID_FILE).catch(() => {});
}
