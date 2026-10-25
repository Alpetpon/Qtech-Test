import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const compose = [
  "compose",
  "-p",
  "qtech-e2e",
  "-f",
  resolve(here, "../../docker-compose.yml"),
  "-f",
  resolve(here, "compose.e2e.yml"),
];

export const baseURL = `http://localhost:${process.env.E2E_PORT ?? "8023"}`;

function docker(...args: string[]) {
  execFileSync("docker", [...compose, ...args], { stdio: "inherit" });
}

export default function setup() {
  docker("build");
  return () => docker("down");
}

export async function fresh() {
  docker("up", "--detach", "--force-recreate");
  const deadline = Date.now() + 60_000;
  for (;;) {
    try {
      if ((await fetch(`${baseURL}/api/health`)).ok) return;
    } catch {
    }
    if (Date.now() > deadline) throw new Error(`Стек e2e не ответил на ${baseURL}/api/health за минуту`);
    await new Promise((done) => setTimeout(done, 500));
  }
}
