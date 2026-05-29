BEGIN;
SELECT setval(pg_get_serial_sequence('"semester"','id'), coalesce(max("id"), 1), max("id") IS NOT null) FROM "semester";
SELECT setval(pg_get_serial_sequence('"batch"','id'), coalesce(max("id"), 1), max("id") IS NOT null) FROM "batch";
SELECT setval(pg_get_serial_sequence('"batchsubject"','id'), coalesce(max("id"), 1), max("id") IS NOT null) FROM "batchsubject";
SELECT setval(pg_get_serial_sequence('"subjectentry"','tid'), coalesce(max("tid"), 1), max("tid") IS NOT null) FROM "subjectentry";
SELECT setval(pg_get_serial_sequence('"staff"','sid'), coalesce(max("sid"), 1), max("sid") IS NOT null) FROM "staff";
SELECT setval(pg_get_serial_sequence('"timetableentry"','tid'), coalesce(max("tid"), 1), max("tid") IS NOT null) FROM "timetableentry";
SELECT setval(pg_get_serial_sequence('"fisat_subjectfacultymap"','id'), coalesce(max("id"), 1), max("id") IS NOT null) FROM "fisat_subjectfacultymap";
COMMIT;
