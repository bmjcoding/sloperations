// Raw SQL for the incident store; the tables are declared in server/db/schema.ts.
export const listIncidents = "SELECT * FROM incidents ORDER BY id";
export const sealIncident = `UPDATE incidents SET sealed_at = now() WHERE id = $1`;
export const addUpdate = `INSERT INTO status_updates (incident_id, body, next_due_at) VALUES ($1, $2, $3)`;
