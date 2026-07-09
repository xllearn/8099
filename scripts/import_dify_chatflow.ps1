param(
    [string]$AppName = "",
    [string]$ApiContainer = "docker-api-1",
    [string]$AccountId = "",
    [string]$TenantId = "",
    [string]$ModelProvider = "langgenius/tongyi/tongyi",
    [string]$ModelName = "qwen3-max-2025-09-23",
    [string]$FastModelProvider = "langgenius/tongyi/tongyi",
    [string]$FastModelName = "qwen-plus-latest",
    [string]$BackendBaseUrl = "http://192.168.34.88:8099",
    [string]$PythonExe = "",
    [switch]$CreateDuplicate
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Builder = Join-Path $ScriptDir "build_dify_chatflow_dsl.py"

if (-not (Test-Path -LiteralPath $Builder)) {
    throw "Missing DSL builder: $Builder"
}

function Invoke-LocalPython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    if ($PythonExe) {
        & $PythonExe @Arguments
        return
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & py -3 @Arguments
        return
    }

    & python @Arguments
}

$TempDsl = Join-Path $env:TEMP ("dify-chatflow-medical-notice-" + [guid]::NewGuid().ToString("N") + ".json")
$ContainerDsl = "/tmp/" + [IO.Path]::GetFileName($TempDsl)

try {
    $BuilderArgs = @(
        $Builder,
        "--output",
        $TempDsl,
        "--model-provider",
        $ModelProvider,
        "--model-name",
        $ModelName,
        "--fast-model-provider",
        $FastModelProvider,
        "--fast-model-name",
        $FastModelName,
        "--backend-base-url",
        $BackendBaseUrl
    )
    if ($AppName) {
        $BuilderArgs += @("--app-name", $AppName)
    }
    Invoke-LocalPython -Arguments $BuilderArgs
    if ($LASTEXITCODE -ne 0) {
        throw "build_dify_chatflow_dsl.py failed with exit code $LASTEXITCODE"
    }

    & docker cp $TempDsl "${ApiContainer}:$ContainerDsl"
    if ($LASTEXITCODE -ne 0) {
        throw "docker cp failed while copying Chatflow DSL into $ApiContainer"
    }

    $CreateDuplicateValue = if ($CreateDuplicate) { "1" } else { "0" }
    $Runner = @'
import argparse
import json
import sys

from sqlalchemy import select

from app import app
from extensions.ext_database import db
from models.account import Account, TenantAccountJoin
from models.model import App as DifyApp
from models.workflow import Workflow
from services.app_dsl_service import AppDslService
from services.entities.dsl_entities import ImportMode, ImportStatus
from services.workflow_service import WorkflowService


def first_account_and_tenant(account_id: str, tenant_id: str):
    if account_id:
        account = db.session.get(Account, account_id)
        if not account:
            raise RuntimeError(f"Account not found: {account_id}")
    else:
        account = db.session.execute(select(Account).limit(1)).scalar_one_or_none()
        if not account:
            raise RuntimeError("No Dify account found.")

    if not tenant_id:
        join = db.session.execute(
            select(TenantAccountJoin).where(TenantAccountJoin.account_id == account.id).limit(1)
        ).scalar_one_or_none()
        if not join:
            raise RuntimeError(f"Account has no workspace join: {account.id}")
        tenant_id = join.tenant_id

    account.set_tenant_id(tenant_id)
    if not account.current_tenant_id:
        raise RuntimeError(f"Account {account.id} cannot access tenant {tenant_id}")
    return account


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsl", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--account-id", default="")
    parser.add_argument("--tenant-id", default="")
    parser.add_argument("--create-duplicate", default="0")
    args = parser.parse_args()

    with app.app_context():
        account = first_account_and_tenant(args.account_id, args.tenant_id)
        yaml_content = open(args.dsl, "r", encoding="utf-8").read()
        dsl_data = json.loads(yaml_content)
        desired_name = args.name or dsl_data.get("app", {}).get("name", "")

        app_id = None
        if args.create_duplicate != "1":
            existing = db.session.execute(
                select(DifyApp).where(
                    DifyApp.tenant_id == account.current_tenant_id,
                    DifyApp.mode == "advanced-chat",
                    DifyApp.name == desired_name,
                )
            ).scalar_one_or_none()
            if not existing:
                existing = db.session.execute(
                    select(DifyApp)
                    .where(
                        DifyApp.tenant_id == account.current_tenant_id,
                        DifyApp.mode == "advanced-chat",
                        DifyApp.name.like("%Chatflow"),
                    )
                    .limit(1)
                ).scalar_one_or_none()
            if existing:
                app_id = existing.id

        service = AppDslService(db.session)
        result = service.import_app(
            account=account,
            import_mode=ImportMode.YAML_CONTENT,
            yaml_content=yaml_content,
            name=args.name or None,
            app_id=app_id,
        )
        if result.status == ImportStatus.FAILED:
            db.session.rollback()
            print(json.dumps({"status": result.status, "error": result.error}, ensure_ascii=False), file=sys.stderr)
            return 1

        app_model = db.session.get(DifyApp, result.app_id)
        workflow_service = WorkflowService()
        workflow = workflow_service.publish_workflow(
            session=db.session,
            app_model=app_model,
            account=account,
            marked_name="Chatflow import",
            marked_comment="Imported by scripts/import_dify_chatflow.ps1",
        )
        app_model.workflow_id = workflow.id
        app_model.updated_by = account.id
        db.session.commit()

        draft = db.session.execute(
            select(Workflow).where(
                Workflow.app_id == app_model.id,
                Workflow.version == Workflow.VERSION_DRAFT,
            )
        ).scalar_one_or_none()

        print(json.dumps({
            "status": str(result.status),
            "app_id": str(app_model.id),
            "app_name": app_model.name,
            "app_mode": app_model.mode,
            "draft_workflow_id": str(draft.id) if draft else "",
            "published_workflow_id": str(workflow.id),
            "app_url": f"http://localhost/app/{app_model.id}/workflow",
        }, ensure_ascii=False, indent=2))
        return 0


raise SystemExit(main())
'@

    $DockerArgs = @(
        "exec",
        "-i",
        $ApiContainer,
        "/app/api/.venv/bin/python",
        "-",
        "--dsl",
        $ContainerDsl,
        "--create-duplicate",
        $CreateDuplicateValue
    )
    if ($AppName) {
        $DockerArgs += @("--name", $AppName)
    }
    if ($AccountId) {
        $DockerArgs += @("--account-id", $AccountId)
    }
    if ($TenantId) {
        $DockerArgs += @("--tenant-id", $TenantId)
    }

    $Runner | docker @DockerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Dify Chatflow import failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -LiteralPath $TempDsl -ErrorAction SilentlyContinue
    & docker exec -u root $ApiContainer rm -f $ContainerDsl 2>$null | Out-Null
}
