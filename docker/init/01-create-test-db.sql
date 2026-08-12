-- Runs once, the first time the MySQL container initialises its data volume.
-- The test suite needs its own schema so a `pytest` run never truncates the
-- database you just demoed from.
CREATE DATABASE IF NOT EXISTS marketplace_test
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

GRANT ALL PRIVILEGES ON marketplace_test.* TO 'marketplace'@'%';
FLUSH PRIVILEGES;
