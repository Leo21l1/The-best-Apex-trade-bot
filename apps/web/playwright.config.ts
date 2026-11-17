import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run the real stack: FastAPI (port 8010) + worker + Next.js (port 3010)
 * against a dedicated `apex_e2e` database which is truncated before the run.
 */
const API_PORT = 8010;
const WEB_PORT = 3010;
const DB = process.env.APEX_E2E_DATABASE_URL ??
  "postgresql+asyncpg://apex:apex_dev_password@localhost:5432/apex_e2e";

const apiEnv = {
  APEX_DATABASE_URL: DB,
  APEX_REDIS_URL: "redis://localhost:6379/14",
  APEX_ENVIRONMENT: "test",
  APEX_CORS_ORIGINS: `["http://localhost:${WEB_PORT}"]`,
  APEX_AUTH_RATE_LIMIT_PER_MINUTE: "100",
  // Clearly-labelled synthetic data so chart workflows can be exercised end to end.
  APEX_MARKET_DATA_PROVIDER: "simulated",
  APEX_MARKET_DATA_SYMBOLS: '["ES","NQ","CL"]',
  // 80h always reaches back past a weekend closure.
  APEX_MARKET_DATA_BACKFILL_HOURS: "80",
};

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: "off",
    ...devices["Desktop Chrome"],
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] }, testIgnore: /responsive\.spec\.ts/ },
    { name: "mobile", use: { ...devices["Pixel 7"] }, testMatch: /responsive\.spec\.ts/ },
  ],
  webServer: [
    {
      command: `../../scripts/e2e-api.sh ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/api/health/live`,
      env: apiEnv,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npx next build && npx next start --port ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}/login`,
      env: {
        APEX_API_ORIGIN: `http://127.0.0.1:${API_PORT}`,
        NEXT_PUBLIC_APEX_WS_URL: `ws://localhost:${API_PORT}/api/ws`,
      },
      reuseExistingServer: false,
      timeout: 240_000,
    },
  ],
});
