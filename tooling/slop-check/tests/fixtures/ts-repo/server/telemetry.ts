// One JSON line per event on stdout; the record layout is described in docs/agent-contract.md.
export type Fields = Record<string, unknown>;

function write(level: string, message: string, fields: Fields): void {
  const record = { time: new Date().toISOString(), level, message, ...fields };
  // eslint-disable-next-line no-console -- the logger is the one module that writes stdout
  console.log(JSON.stringify(record));
}

export const telemetry = {
  info: (message: string, fields: Fields = {}) => write("info", message, fields),
  error: (message: string, fields: Fields = {}) => write("error", message, fields),
};
