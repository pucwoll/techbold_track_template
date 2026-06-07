import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.PLAYWRIGHT_VITE_PORT ?? 5174);
const host = "127.0.0.1";
const baseURL = `http://${host}:${port}`;

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [["list"]],
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: `VITE_API_BASE=http://127.0.0.1:18080 pnpm exec vite --host ${host} --port ${port}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
  },
});
