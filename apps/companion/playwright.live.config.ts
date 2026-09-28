import { defineConfig, devices } from "@playwright/test";
export default defineConfig({ testDir: "./tests/e2e", retries: 0, reporter: [["list"]], use: { baseURL: "http://localhost:3002", trace: "retain-on-failure" }, projects: [{ name: "chrome", use: { ...devices["Desktop Chrome"], channel: "chrome" } }] });
