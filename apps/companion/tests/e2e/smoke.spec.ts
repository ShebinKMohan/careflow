import { mkdirSync } from "node:fs";

import { expect, type Page, test } from "@playwright/test";

const FOOTER_EN = "Research prototype. Synthetic data only. Not for clinical use.";
const DISCLAIMER_EN = "Arabic supported; not validated by native speakers.";
const EVIDENCE_DIR = new URL("../../../../docs/evidence/M0-08/", import.meta.url).pathname;

async function claim(page: Page) {
  await page.goto("/");
  await page.getByLabel("Hand-over code").fill("example-qr-token-0001");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Connected, L. K." })).toBeVisible();
}

/** Every URL in every Cache Storage cache of this origin. */
async function cachedUrls(page: Page): Promise<string[]> {
  return page.evaluate(async () => {
    const urls: string[] = [];
    for (const name of await caches.keys()) {
      for (const request of await (await caches.open(name)).keys()) urls.push(request.url);
    }
    return urls;
  });
}

test("the footer is visible on every page", async ({ page }) => {
  for (const path of ["/", "/status"]) {
    await page.goto(path);
    await expect(page.getByTestId("prototype-footer"), path).toHaveText(FOOTER_EN);
    await expect(page.getByTestId("arabic-disclaimer"), path).toHaveText(DISCLAIMER_EN);
    await expect(page.getByRole("contentinfo"), path).toBeVisible();
  }
});

test("the QR claim and the queue status render the mocked data", async ({ page }) => {
  await claim(page);
  await page.getByRole("link", { name: "See my place in the queue" }).click();
  await expect(page.getByText("Your number: G-031")).toBeVisible();
  await expect(page.getByText("Position: 3")).toBeVisible();
});

test("the manifest makes the app installable", async ({ request }) => {
  const response = await request.get("/manifest.webmanifest");
  expect(response.status()).toBe(200);
  const manifest = (await response.json()) as {
    name: string;
    display: string;
    start_url: string;
    icons: { src: string; sizes: string }[];
  };
  expect(manifest.name).toBe("CareFlow companion");
  expect(manifest.display).toBe("standalone");
  expect(manifest.start_url).toBe("/");
  expect(manifest.icons.map((icon) => icon.sizes).sort()).toEqual(["192x192", "512x512"]);
  for (const icon of manifest.icons) {
    const image = await request.get(icon.src);
    expect(image.status(), icon.src).toBe(200);
    expect(image.headers()["content-type"]).toBe("image/png");
  }
});

test("the service worker activates and never serves or caches an API response", async ({ page }) => {
  await page.goto("/");
  // `ready` resolves once a worker is active, which can still be "activating" (the activate handler
  // runs); wait for that step to finish before checking.
  const state = await page.evaluate(async () => {
    const worker = (await navigator.serviceWorker.ready).active;
    if (worker && worker.state !== "activated") {
      await new Promise<void>((resolve) => {
        worker.addEventListener("statechange", () => {
          if (worker.state === "activated") resolve();
        });
      });
    }
    return worker?.state;
  });
  expect(state).toBe("activated");
  await page.reload(); // now controlled by the worker
  expect(await page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);

  await claim(page);
  const statusResponse = page.waitForResponse((response) => response.url().includes("/v1/companion/status"));
  await page.getByRole("link", { name: "See my place in the queue" }).click();
  const response = await statusResponse;
  expect(response.status()).toBe(200);
  expect(response.fromServiceWorker()).toBe(false);
  await expect(page.getByText("Your number: G-031")).toBeVisible();

  const urls = await cachedUrls(page);
  expect(urls.length).toBeGreaterThan(0);
  expect(urls.filter((url) => url.includes("/v1/") || !url.startsWith(new URL(page.url()).origin))).toEqual([]);
  expect(urls.some((url) => url.endsWith("/status"))).toBe(false);

  await page.evaluate(() => document.fonts.ready); // stable screenshots: web fonts loaded
  mkdirSync(EVIDENCE_DIR, { recursive: true });
  await page.screenshot({ path: `${EVIDENCE_DIR}companion-status.png`, fullPage: true });
});
