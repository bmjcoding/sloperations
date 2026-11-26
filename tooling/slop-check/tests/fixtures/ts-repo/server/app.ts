import { telemetry } from "./telemetry.ts";

export function handle(request: Request, routeName: string, response: { statusCode: number }, started: number): void {
  const ms = Date.now() - started;
  telemetry.info("request", { method: request.method, route: routeName, status: response.statusCode, ms });
}
