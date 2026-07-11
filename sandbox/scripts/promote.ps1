# Promote sandbox -> root (Windows PowerShell)
# Uso (desde la carpeta sandbox):
#   .\scripts\promote.ps1
# Opcional: -DryRun para solo mostrar acciones

param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$SandboxRoot = Split-Path -Parent $PSScriptRoot
$RepoRoot = Split-Path -Parent $SandboxRoot

Write-Host "Repo root: $RepoRoot"
Write-Host "Sandbox:   $SandboxRoot"

function Invoke-Step($msg, $action) {
    Write-Host "`n==> $msg"
    if ($DryRun) {
        Write-Host "  [dry-run] skipped"
        return
    }
    & $action
}

# 1) Backup legacy app
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = Join-Path $RepoRoot "legacy_backup_$stamp"

Invoke-Step "Backup legacy app/ -> $backup" {
    New-Item -ItemType Directory -Path $backup | Out-Null
    if (Test-Path (Join-Path $RepoRoot "app")) {
        Copy-Item -Recurse (Join-Path $RepoRoot "app") (Join-Path $backup "app")
    }
    foreach ($f in @("Dockerfile", "requirements.txt", ".env.example")) {
        $src = Join-Path $RepoRoot $f
        if (Test-Path $src) { Copy-Item $src $backup }
    }
}

# 2) Replace app/
Invoke-Step "Replace root app/ with sandbox/app" {
    $dest = Join-Path $RepoRoot "app"
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Copy-Item -Recurse (Join-Path $SandboxRoot "app") $dest
}

# 3) Copy web/
Invoke-Step "Copy sandbox/web -> root web/" {
    $dest = Join-Path $RepoRoot "web"
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Copy-Item -Recurse (Join-Path $SandboxRoot "web") $dest
}

# 4) Replace packaging files
Invoke-Step "Replace Dockerfile, requirements.txt, .env.example" {
    Copy-Item (Join-Path $SandboxRoot "Dockerfile") (Join-Path $RepoRoot "Dockerfile") -Force
    Copy-Item (Join-Path $SandboxRoot "requirements.txt") (Join-Path $RepoRoot "requirements.txt") -Force
    Copy-Item (Join-Path $SandboxRoot ".env.example") (Join-Path $RepoRoot ".env.example") -Force
}

# 5) Copy docs
Invoke-Step "Copy migration docs into docs/" {
    $docs = Join-Path $RepoRoot "docs"
    Copy-Item (Join-Path $SandboxRoot "docs\ARCHITECTURE.md") (Join-Path $docs "ARCHITECTURE_REWORK.md") -Force
    Copy-Item (Join-Path $SandboxRoot "docs\MIGRATION_CHECKLIST.md") (Join-Path $docs "MIGRATION_CHECKLIST.md") -Force
}

Write-Host "`nPromocion lista."
Write-Host "Siguiente:"
Write-Host "  1. Revisar git status / diff"
Write-Host "  2. Actualizar .env de produccion (Meta + DATABASE_URL)"
Write-Host "  3. git add -A && commit && push"
Write-Host "  4. Redeploy EasyPanel + webhook Meta"
Write-Host "  5. Seguir docs/MIGRATION_CHECKLIST.md"
