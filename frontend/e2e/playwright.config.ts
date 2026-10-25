import { defineConfig, devices } from "@playwright/test";

import { baseURL } from "./stack";

export default defineConfig({
  testDir: ".",
  globalSetup: "./stack.ts",
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: "list",
  use: { baseURL, trace: "retain-on-failure" },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
