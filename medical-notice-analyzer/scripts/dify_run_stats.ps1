param(
    [string]$WorkflowRunId = "",
    [int]$Latest = 1,
    [string]$PostgresContainer = "docker-db_postgres-1",
    [string]$Database = "dify",
    [string]$User = "postgres",
    [string]$Password = $env:DIFY_POSTGRES_PASSWORD
)

$ErrorActionPreference = "Stop"

if (-not $Password) {
    throw "Set DIFY_POSTGRES_PASSWORD or pass -Password before running this script."
}

if ($Latest -lt 1) {
    throw "-Latest must be greater than or equal to 1."
}

if ($WorkflowRunId -and $WorkflowRunId -notmatch "^[0-9a-fA-F-]{36}$") {
    throw "-WorkflowRunId must be a UUID."
}

function Invoke-DifyPsql {
    param(
        [string]$Sql
    )

    docker exec -e "PGPASSWORD=$Password" $PostgresContainer psql -U $User -d $Database -P pager=off -c $Sql
    if ($LASTEXITCODE -ne 0) {
        throw "docker exec failed while reading Dify workflow statistics."
    }
}

if ($WorkflowRunId) {
    $selectedRuns = "select wr.id from workflow_runs wr where wr.id = '$WorkflowRunId'"
} else {
    $selectedRuns = "select wr.id from workflow_runs wr order by wr.created_at desc limit $Latest"
}

$runSql = @"
with selected_runs as (
  $selectedRuns
)
select
  'Dify workflow run total' as section,
  wr.id as workflow_run_id,
  wr.status,
  wr.total_tokens,
  round(wr.elapsed_time::numeric, 3) as elapsed_seconds,
  wr.total_steps,
  wr.created_at,
  wr.finished_at
from workflow_runs wr
join selected_runs sr on sr.id = wr.id
order by wr.created_at desc;
"@

$nodeSql = @"
with selected_runs as (
  $selectedRuns
)
select
  'Dify node totals' as section,
  wne.workflow_run_id,
  wne.index,
  wne.title,
  wne.node_type,
  wne.status,
  coalesce((wne.execution_metadata::jsonb ->> 'total_tokens')::bigint, 0) as total_tokens,
  round(wne.elapsed_time::numeric, 3) as elapsed_seconds,
  wne.execution_metadata
from workflow_node_executions wne
join selected_runs sr on sr.id = wne.workflow_run_id
order by wne.workflow_run_id, wne.index;
"@

Write-Host "Dify workflow run total"
Invoke-DifyPsql -Sql $runSql

Write-Host ""
Write-Host "Dify node totals"
Invoke-DifyPsql -Sql $nodeSql
