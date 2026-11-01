-- Test-only: a migration later than the shipped ones, to prove that the role guard applies to every migration.
CREATE TABLE project_ops.later_probe (id integer PRIMARY KEY);
