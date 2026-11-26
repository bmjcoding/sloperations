import { telemetry } from "./telemetry.ts";

export function answered(model: string, usage: { inputTokens: number; outputTokens: number }): void {
  telemetry.info("scribe answer", { model, ...usage });
}
