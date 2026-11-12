import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 20000,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:4173${process.env.MERCURY_BASE_PATH ?? ""}/`,
    headless: true,
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {},
  },
  webServer: {
    command: "node tests/serve.mjs",
    url: `http://127.0.0.1:4173${process.env.MERCURY_BASE_PATH ?? ""}/`,
    reuseExistingServer: false,
    timeout: 10000,
  },
});
