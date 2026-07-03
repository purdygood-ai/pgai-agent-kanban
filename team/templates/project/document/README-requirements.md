# Requirements Documents (document workflow)

Drop one document per deliverable. Each ships as its own version.

Naming: `vX.Y.Z-<slug>-YYYYMMDD.md` — explicit target version, slug describing
the deliverable, date.

Structure: see templates/REQUIREMENTS-TEMPLATE.md. The "Brief" section is the
WRITER's primary input — make it concrete (audience, outline, source material).

The framework picks up the lowest target_version > Last Released; ships as
that version through the document workflow:
  PM -> WRITER outline -> WRITER draft (foreach sections) -> WRITER integrate
  -> WRITER polish -> TESTER review -> CM finalize.

Sample brief example: see brief-example.md in the project root.
