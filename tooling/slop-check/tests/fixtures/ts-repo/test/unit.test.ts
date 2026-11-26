import { test } from "node:test";
import { telemetry } from "../server/telemetry.ts";

// TODO(#12): replace the synthetic event with the request handler once it takes a sink.
test("writes one JSON line", () => {
  telemetry.info("hello", {});
});
