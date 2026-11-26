CREATE TABLE "incidents" (
	"id" serial PRIMARY KEY NOT NULL,
	"title" text NOT NULL,
	"sealed_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "status_updates" (
	"id" serial PRIMARY KEY NOT NULL,
	"incident_id" integer NOT NULL REFERENCES "incidents"("id"),
	"body" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "incident_tags" (
	"incident_id" integer NOT NULL,
	"tag" text NOT NULL,
	CONSTRAINT "incident_tags_pk" PRIMARY KEY("incident_id","tag")
);
