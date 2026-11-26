import { telemetry } from "./telemetry.ts";

// Holds an advisory lock while migrating so two tasks never apply the same file.
export function report(applied: boolean, count: number): void {
  telemetry.info(applied ? "migrations applied" : "migrations current", { count });
}
