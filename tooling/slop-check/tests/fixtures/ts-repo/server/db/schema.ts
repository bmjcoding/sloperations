import { integer, pgTable, primaryKey, serial, text, timestamp } from "drizzle-orm/pg-core";

export const incidents = pgTable("incidents", {
  id: serial("id").primaryKey(),
  title: text("title").notNull(),
  sealedAt: timestamp("sealed_at", { withTimezone: true }).defaultNow(),
});

export const statusUpdates = pgTable("status_updates", {
  id: serial("id").primaryKey(),
  incidentId: integer("incident_id").notNull().references(() => incidents.id),
  body: text("body").notNull(),
  nextDueAt: timestamp("next_due_at", { withTimezone: true }),
});

export const incidentTags = pgTable("incident_tags", {
  incidentId: integer("incident_id").notNull(),
  tag: text("tag").notNull(),
}, (t) => [primaryKey({ columns: [t.incidentId, t.tag] })]);
