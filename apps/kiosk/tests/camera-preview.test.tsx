import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CameraPreview } from "@/components/CameraPreview";
import { markConsentSaved, setConsentChoices } from "@/consent/hasConsent";
import { MESSAGES } from "@/i18n/messages";
import { KioskProvider, useKiosk } from "@/lib/session";

const getUserMedia = vi.fn();
const continueIntake = vi.fn();
const CHOICES = { camera: true, voice: false, dataUse: true };
const QUESTION = { question_id: "q_camera", text: "When did this start?", key: "symptom_onset_at" as const, notice: null };
const PATIENT = { session_id: "ses_camera", session_token: "synthetic-patient-token", patient_display: "A. R.", language: "en" as const };
const originalMediaDevices = Object.getOwnPropertyDescriptor(navigator, "mediaDevices");
const T = MESSAGES.en.cameraPreview;
let hidden = false;

function fakeStream() {
  const tracks = [{ stop: vi.fn() }, { stop: vi.fn() }];
  const media = { getTracks: () => tracks } as unknown as MediaStream;
  return { media, tracks };
}

function show({ saved = true, camera = true, dataUse = true, patient = true } = {}) {
  const seen: { current: ReturnType<typeof useKiosk> | null } = { current: null };
  function Harness() {
    const kiosk = useKiosk();
    useEffect(() => { seen.current = kiosk; });
    return <><CameraPreview /><button type="button" onClick={continueIntake}>Continue intake</button></>;
  }
  const result = render(
    <NextIntlClientProvider locale="en" messages={MESSAGES.en} timeZone="UTC">
      <KioskProvider><Harness /></KioskProvider>
    </NextIntlClientProvider>,
  );
  act(() => {
    seen.current!.setConsent({ ...CHOICES, camera, dataUse });
    if (saved) markConsentSaved();
    if (patient) seen.current!.start(PATIENT, QUESTION);
  });
  return { ...result, wipe: () => act(() => seen.current!.wipe()) };
}

beforeEach(() => {
  getUserMedia.mockReset();
  continueIntake.mockReset();
  hidden = false;
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => hidden ? "hidden" : "visible");
  setConsentChoices(null);
});

afterEach(() => {
  setConsentChoices(null);
  vi.restoreAllMocks();
  if (originalMediaDevices) Object.defineProperty(navigator, "mediaDevices", originalMediaDevices);
  else Reflect.deleteProperty(navigator, "mediaDevices");
});

describe("the consented local camera preview", () => {
  it("starts only after an explicit click and requests video without microphone access", async () => {
    const { media } = fakeStream();
    getUserMedia.mockResolvedValue(media);
    show();
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(screen.queryByText(T.live)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: T.start }));

    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(getUserMedia).toHaveBeenCalledWith({
      audio: false,
      video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
    });
    expect(screen.getByLabelText(T.videoLabel)).toHaveProperty("srcObject", media);
    expect(screen.getByText(T.live)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: T.stop })).toBeEnabled();
  });

  it.each([
    ["unsaved consent", { saved: false }],
    ["declined camera", { camera: false }],
    ["declined data use", { dataUse: false }],
    ["no patient session", { patient: false }],
  ] as const)("never opens or requests a camera with %s", async (_name, options) => {
    show(options);
    expect(screen.queryByRole("button", { name: T.start })).not.toBeInTheDocument();
    expect(getUserMedia).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Continue intake" }));
    expect(continueIntake).toHaveBeenCalledTimes(1);
  });

  it.each(["toggle", "unmount", "wipe", "hidden", "pagehide"] as const)("stops every active track on %s", async (event) => {
    const { media, tracks } = fakeStream();
    getUserMedia.mockResolvedValue(media);
    const view = show();
    await userEvent.click(screen.getByRole("button", { name: T.start }));
    const video = screen.getByLabelText(T.videoLabel);

    if (event === "toggle") await userEvent.click(screen.getByRole("button", { name: T.stop }));
    else if (event === "unmount") view.unmount();
    else if (event === "wipe") view.wipe();
    else if (event === "pagehide") fireEvent(window, new Event("pagehide"));
    else { hidden = true; fireEvent(document, new Event("visibilitychange")); }

    for (const track of tracks) expect(track.stop).toHaveBeenCalledTimes(1);
    // React may detach the video ref before an unmount cleanup, but all capture has stopped.
    if (event !== "unmount") expect(video).toHaveProperty("srcObject", null);
    if (event === "toggle" || event === "hidden") expect(screen.getByRole("button", { name: T.start })).toBeEnabled();
  });

  it.each(["cancel", "unmount", "wipe", "hidden"] as const)("stops a late permission stream after %s instead of reviving capture", async (event) => {
    const { media, tracks } = fakeStream();
    let grant!: (stream: MediaStream) => void;
    getUserMedia.mockReturnValue(new Promise<MediaStream>((resolve) => { grant = resolve; }));
    const view = show();
    await userEvent.click(screen.getByRole("button", { name: T.start }));
    expect(screen.getByRole("button", { name: T.cancel })).toBeEnabled();

    if (event === "cancel") await userEvent.click(screen.getByRole("button", { name: T.cancel }));
    else if (event === "unmount") view.unmount();
    else if (event === "wipe") view.wipe();
    else { hidden = true; fireEvent(document, new Event("visibilitychange")); }
    await act(async () => { grant(media); });

    for (const track of tracks) expect(track.stop).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(T.live)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: T.stop })).not.toBeInTheDocument();
    expect(getUserMedia).toHaveBeenCalledTimes(1);
  });

  it("keeps intake usable after permission denial and permits an explicit retry", async () => {
    const { media } = fakeStream();
    getUserMedia.mockRejectedValueOnce(new DOMException("Permission denied", "NotAllowedError"));
    getUserMedia.mockResolvedValueOnce(media);
    show();
    await userEvent.click(screen.getByRole("button", { name: T.start }));

    expect(screen.getByRole("status")).toHaveTextContent(T.denied);
    expect(screen.getByRole("button", { name: T.start })).toBeEnabled();
    expect(screen.queryByText(T.live)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Continue intake" }));
    expect(continueIntake).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: T.start }));
    expect(screen.getByRole("button", { name: T.stop })).toBeEnabled();
    expect(getUserMedia).toHaveBeenCalledTimes(2);
  });
});
