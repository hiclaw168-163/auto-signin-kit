-- 台账索引(依赖迁移后的列名,必须在 _migrate 补列改名之后执行)。
-- 历史沿革产生的三种旧索引名都在这里清掉(无前缀版是最早的表名遗留),
-- 避免老库残留重复索引。
DROP INDEX IF EXISTS idx_signin_runs_ts;
DROP INDEX IF EXISTS idx_ask_signin_runs_ts;
CREATE INDEX IF NOT EXISTS idx_ask_signin_runs_executed_at
    ON ask_signin_runs(executed_at);
DROP INDEX IF EXISTS idx_signin_runs_domain_ts;
DROP INDEX IF EXISTS idx_ask_signin_runs_domain_ts;
CREATE INDEX IF NOT EXISTS idx_ask_signin_runs_domain_executed_at
    ON ask_signin_runs(domain, executed_at);
CREATE INDEX IF NOT EXISTS idx_ask_signin_runs_source ON ask_signin_runs(source);
