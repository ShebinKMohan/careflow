import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { nurseSetsLevel, staffPage } from "../lib/actors";
import { STACK } from "../lib/stack";

const evidence = resolve(import.meta.dirname, "../../docs/evidence/RECEPTION-20260928");
const timings: Record<string, number> = {};
async function shot(page: Page, name: string) {
  mkdirSync(`${evidence}/screenshots`, {recursive:true});
  await page.screenshot({path:`${evidence}/screenshots/${name}.png`, fullPage:true});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${name} has no horizontal overflow`).toBe(true);
}
async function next(page:Page, name:string, ready:()=>Promise<unknown>) {
  const began=Date.now(); await page.getByRole("button",{name:"Continue",exact:true}).click(); await ready();
  timings[name]=Date.now()-began;
  expect(timings[name], "client-only stage navigation under two seconds").toBeLessThan(2_000);
}
async function entryTicket(page:Page) {
  const issued=page.waitForResponse(r=>r.url().endsWith("/v1/checkin/start/qr") && r.request().method()==="POST");
  await page.goto(STACK.kiosk);
  const response=await issued; expect(response.status()).toBe(200);
  await expect(page.getByTestId("entry-qr")).toBeVisible();
  return String((await response.json()).qr_token);
}
test.afterAll(()=>{mkdirSync(evidence,{recursive:true});writeFileSync(`${evidence}/stage-timings.json`,JSON.stringify(timings,null,2));});

test("new kiosk registration retains edits, saves guided vitals, and reaches nurse review",async({page,browser})=>{
  const turns:string[]=[];page.on("request",r=>{if(/\/turns$/.test(r.url()))turns.push(r.url());});
  await entryTicket(page);await shot(page,"kiosk-welcome");
  await page.getByRole("button",{name:"Start on this kiosk",exact:true}).click();
  await page.getByRole("button",{name:"Agree",exact:true}).click();
  await expect(page.getByLabel("Full name",{exact:true})).toBeVisible();
  await page.getByLabel("Full name",{exact:true}).fill("Taylor Demo Reception");
  await page.getByLabel("Age in years",{exact:true}).fill("34");
  await page.getByRole("button",{name:"Female",exact:true}).click();
  await shot(page,"kiosk-registration");
  await next(page,"kiosk_details_to_departments",()=>expect(page.getByRole("button",{name:/^ENT Ear/})).toBeVisible());
  await expect(page.locator(".reception-choice-grid > .reception-choice-card")).toHaveCount(12);
  await shot(page,"kiosk-departments");
  await page.getByRole("button",{name:"Back",exact:true}).click();
  await expect(page.getByLabel("Full name",{exact:true})).toHaveValue("Taylor Demo Reception");
  await page.getByRole("button",{name:"Continue",exact:true}).click();
  await page.getByRole("button",{name:/^ENT Ear/}).click();
  await shot(page,"kiosk-doctors");
  await page.getByRole("button",{name:/Dr Samir Khan/}).click();
  await page.getByRole("button",{name:"New consultation",exact:true}).click();
  await page.getByLabel("Tell us a little more",{exact:false}).fill("Synthetic ear discomfort for two days");
  await shot(page,"kiosk-visit");
  await next(page,"kiosk_reason_to_review",()=>expect(page.getByRole("button",{name:"Submit my registration",exact:true})).toBeVisible());
  await page.getByRole("button",{name:"Edit",exact:true}).nth(2).click();
  await expect(page.getByLabel("Tell us a little more",{exact:false})).toHaveValue("Synthetic ear discomfort for two days");
  await page.getByRole("button",{name:"Continue",exact:true}).click();await shot(page,"kiosk-review");
  const registered=page.waitForResponse(r=>r.url().endsWith("/v1/reception/checkin")&&r.request().method()==="POST");
  const began=Date.now();await page.getByRole("button",{name:"Submit my registration",exact:true}).click();
  const response=await registered;timings.kiosk_registration_api=Date.now()-began;expect(response.status()).toBe(200);
  const registration=await response.json();expect(timings.kiosk_registration_api).toBeLessThan(15_000);
  await expect(page).toHaveURL(/\/vitals$/);await shot(page,"kiosk-vitals-ready");
  await page.getByRole("button", {name:"Back to visit summary", exact:true}).click();
  await expect(page.getByRole("heading", {name:"Your saved visit summary",exact:true})).toBeVisible();
  await expect(page.getByText("Taylor Demo Reception",{exact:true})).toBeVisible();
  await shot(page,"kiosk-saved-visit");
  await page.getByRole("button", {name:"Return to health check", exact:true}).click();
  const measuring=Date.now();await page.getByRole("button",{name:"Start demo measurement",exact:true}).click();await shot(page,"kiosk-vitals-measuring");
  await expect(page).toHaveURL(/\/done$/,{timeout:38_000});timings.kiosk_guided_vitals=Date.now()-measuring;
  expect(timings.kiosk_guided_vitals).toBeGreaterThanOrEqual(9_500);await shot(page,"kiosk-complete");
  const status=await page.request.get(`${STACK.api}/v1/sessions/${registration.session_id}/status`,{headers:{Authorization:`Bearer ${registration.session_token}`}});
  expect(status.status()).toBe(200);const saved=await status.json();expect(saved.vitals.simulated).toBe(true);expect(saved.vitals.heart_rate).toBeGreaterThan(0);expect(saved.vitals.spo2).toBeGreaterThan(0);
  expect(turns).toEqual([]);
  const nurse=await staffPage(browser,"nurse","/nurse");await nurse.setViewportSize({width:1280,height:800});
  const row=nurse.locator(`a[href="/nurse/${registration.session_id}"]`);await expect(row).toBeVisible({timeout:25_000});
  await row.click();await expect(nurse.getByTestId("reception-summary")).toContainText("Taylor Demo Reception");
  await expect(nurse.getByTestId("reception-summary")).toContainText("Dr Samir Khan");
  await shot(nurse,"nurse-reception-review");
  const decisionStarted = Date.now();
  const decisionResponse = nurse.waitForResponse(response => response.url().endsWith(`/v1/nurse/sessions/${registration.session_id}/decision`) && response.request().method() === "POST", {timeout:45_000});
  await nurseSetsLevel(nurse, 4);
  expect((await decisionResponse).status()).toBe(200);
  timings.nurse_decision_api = Date.now() - decisionStarted;
  const queueStarted = Date.now();
  await expect.poll(async () => {
    const response = await page.request.get(`${STACK.api}/v1/sessions/${registration.session_id}/status`, {headers:{Authorization:`Bearer ${registration.session_token}`}});
    return (await response.json()).token_code;
  }, {timeout:25_000}).toMatch(/^[A-Z]+-?[0-9]+$/);
  timings.nurse_decision_to_queue_status = Date.now() - queueStarted;
  await expect(page.getByTestId("token-code")).toBeVisible();
  await shot(page,"kiosk-queue-ticket");await nurse.context().close();
});

test("phone scans a real entry ticket and registers without device login or conversational intake",async({page,browser})=>{
  const ticket=await entryTicket(page);
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});const phone=await context.newPage();
  const turns:string[]=[];phone.on("request",r=>{if(/\/turns$/.test(r.url()))turns.push(r.url());});
  await phone.goto(`${STACK.companion}/start?l=en#t=${ticket}`);
  await phone.getByRole("checkbox").check();await shot(phone,"phone-permission");
  await phone.getByRole("button",{name:"Continue",exact:true}).click();
  await phone.getByLabel("Patient’s full name",{exact:true}).fill("Jordan Demo Reception");
  await phone.getByLabel("Age in years",{exact:true}).fill("29");
  await phone.getByRole("combobox").selectOption("male");await shot(phone,"phone-registration");
  await next(phone,"phone_details_to_departments",()=>expect(phone.getByRole("button",{name:/^Ear, nose and throat/})).toBeVisible());
  await phone.getByRole("button",{name:/^Ear, nose and throat/}).click();await phone.getByLabel("Doctor preference (optional)",{exact:false}).selectOption("demo_ent_01");
  await shot(phone,"phone-care-team");await phone.getByRole("button",{name:"Continue",exact:true}).click();
  await phone.getByLabel("Reason for your visit",{exact:false}).fill("Synthetic ear follow-up visit");await shot(phone,"phone-visit");
  await phone.getByRole("button",{name:"Review my registration",exact:true}).click();
  await phone.getByRole("button",{name:"Back",exact:true}).click();
  await expect(phone.getByLabel("Reason for your visit",{exact:false})).toHaveValue("Synthetic ear follow-up visit");
  await phone.getByRole("button",{name:"Review my registration",exact:true}).click();await shot(phone,"phone-review");
  const responsePromise=phone.waitForResponse(r=>r.url().endsWith("/v1/reception/checkin")&&r.request().method()==="POST");
  const began=Date.now();await phone.getByRole("button",{name:"Register my visit",exact:true}).click();
  const registered=await responsePromise;timings.phone_registration_api=Date.now()-began;expect(registered.status()).toBe(200);
  expect(timings.phone_registration_api).toBeLessThan(30_000);await expect(phone).toHaveURL(/\/status$/);await expect(phone.getByRole("heading", {name:"Waiting for nurse review", exact:true})).toBeVisible();await expect(phone.getByText("Dr Samir Khan", {exact:true})).toBeVisible();await shot(phone,"phone-waiting");
  expect(turns).toEqual([]);expect(new URL(phone.url()).hash).toBe("");await context.close();
});
