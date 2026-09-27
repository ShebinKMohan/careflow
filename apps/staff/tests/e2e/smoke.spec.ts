import { expect, type Page, test } from "@playwright/test";

const FOOTER_EN = "Research prototype. Synthetic data only. Not for clinical use.";
const PAGES = ["/", "/login", "/nurse", "/nurse/ses_chest_pain", "/doctor/ses_chest_pain", "/board?department=ed", "/admin"];

async function signIn(page: Page, next: string) {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Username").fill("nurse.a@careflow.example");
  await page.getByLabel("Password").fill("example-password");
  await page.getByRole("main").getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(new RegExp(`${next.replace(/[?]/g, "\\?")}$`));
}

test("the footer is visible on every page", async ({ page }) => {
  for (const path of PAGES) {
    await page.goto(path);
    await expect(page.getByRole("contentinfo"), path).toHaveText(FOOTER_EN);
    await expect(page.getByRole("contentinfo"), path).toBeVisible();
  }
});

test("screens ask for sign-in without a token", async ({ page }) => {
  await page.goto("/nurse");
  await expect(page.getByText("Please sign in to see this screen.")).toBeVisible();
});

test("the nurse worklist renders the mocked sessions", async ({ page }) => {
  await signIn(page, "/nurse");
  const rows = page.getByRole("row");
  await expect(rows.filter({ hasText: "M. S." })).toContainText("Critical: Chest pain: ECG in 10 min");
  await expect(rows.filter({ hasText: "L. K." })).toBeVisible();
});

test("the doctor briefing shows provenance, the board and admin render their data", async ({ page }) => {
  await signIn(page, "/doctor/ses_chest_pain");
  await expect(page.getByText("Crushing chest pain for about an hour")).toBeVisible();
  await expect(page.getByText("PATIENT_STATED").first()).toBeVisible();
  await page.goto("/board?department=ed");
  await expect(page.getByRole("cell", { name: "A-014" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Resus 2" })).toBeVisible();
  await page.goto("/admin");
  await expect(page.getByRole("cell", { name: "qwen3.5-9b-4bit" })).toBeVisible();
});
