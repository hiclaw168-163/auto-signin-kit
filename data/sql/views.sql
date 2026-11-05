-- 台账派生视图(纯派生不落库——派生数据落库会引入两份真相)。
-- CREATE VIEW 无 IF NOT EXISTS 的改列能力,直接 DROP+CREATE 保证老库也
-- 能拿到新列;必须在 _migrate 之后、BEGIN IMMEDIATE 原子块内执行
-- (多进程并发 connect 时两个 executescript 交错会撞 "already exists")。
-- 输出列名避开数据库关键字(day→checkin_day, account→account_name)。

-- 每天每域每账户最后一次运行 = 当天最终状态,把"一天多次运行"折叠成一行,
-- Web 台账页与人工查库都读它。executed_at 已统一为本地无时区(迁移抹掉旧
-- +08:00 后缀),裸 date() 直接取日期部分;折叠维度是 (day, domain, account)
-- (SPEC-12): 多账户各自有"当天最终状态",按 (day, domain) 折叠会把 B 账户
-- 的最终行折叠掉。
DROP VIEW IF EXISTS ask_daily_checkins;
CREATE VIEW ask_daily_checkins AS
SELECT date(r.executed_at)   AS checkin_day,
       r.domain              AS domain,
       r.account_name        AS account_name,
       r.result              AS result,
       r.source              AS source,
       r.needs_attention     AS needs_attention,
       r.report              AS report,
       r.executed_at         AS executed_at
FROM ask_signin_runs AS r
WHERE r.id = (
    SELECT r2.id FROM ask_signin_runs AS r2
    WHERE date(r2.executed_at) = date(r.executed_at) AND r2.domain = r.domain
      AND r2.account_name = r.account_name
    ORDER BY r2.executed_at DESC, r2.id DESC
    LIMIT 1
);

-- 每日积分: 一行 = (日期, 域, 账户) 当天积分所得。两个口径要点:
-- ①字段方言收敛: qoder/trae 报 credits,workbuddy 的 CLAIMED 报 credit、
--   ALREADY 报 today_credit(当日已领快照),COALESCE 取首个非空;
--   workbuddy 的 score 是活跃度分、total_credits 是累计值,都绝不计入;
-- ②同域同天多行(高密度调度幂等重跑)按 MAX 取当天快照,不能 SUM 累加。
-- 结果词集合须与 core.report.RESULT_OK 同步(4 词,改动时两边一起改)。
DROP VIEW IF EXISTS ask_daily_credits;
CREATE VIEW ask_daily_credits AS
SELECT checkin_day, domain, account_name, MAX(credits_val) AS credits
FROM (
    SELECT date(executed_at) AS checkin_day,
           domain            AS domain,
           account_name      AS account_name,
           COALESCE(
               json_extract(response, '$.credits'),
               json_extract(response, '$.credit'),
               json_extract(response, '$.today_credit')
           ) AS credits_val
    FROM ask_signin_runs
    WHERE source = 'run'
      AND result IN ('CLAIMED', 'ALREADY', 'ACTIVE', 'OK')
)
WHERE credits_val IS NOT NULL
GROUP BY checkin_day, domain, account_name;
