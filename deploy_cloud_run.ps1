param(
    [Parameter(Mandatory=$false)]
    [string]$ProjectId = "weekly-sinokor-schedule-meet",

    [Parameter(Mandatory=$false)]
    [string]$ServiceName = "cix2-service-monitor",

    [Parameter(Mandatory=$false)]
    [string]$Region = "asia-northeast3"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$gcloud = "gcloud"
if (-not (Get-Command $gcloud -ErrorAction SilentlyContinue)) {
    $localGcloud = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
    if (Test-Path $localGcloud) {
        $gcloud = $localGcloud
    } else {
        Write-Host "❌ gcloud CLI가 설치되어 있지 않습니다." -ForegroundColor Red
        exit 1
    }
}

Write-Host "📦 스냅샷 한글 파일명 UTF-8 번들 생성 중..." -ForegroundColor Cyan
python -c "import tarfile, pathlib;
with tarfile.open('snapshots_bundle.tar.gz', 'w:gz', encoding='utf-8') as tf:
    for f in sorted(pathlib.Path('snapshots').glob('*.html')):
        tf.add(f, arcname=f'snapshots/{f.name}')"

Write-Host "🚀 Google Cloud Run에 '$ServiceName' 배포를 시작합니다 (리전: $Region)..." -ForegroundColor Green
& $gcloud run deploy $ServiceName `
    --source . `
    --project $ProjectId `
    --region $Region `
    --allow-unauthenticated `
    --memory 2Gi `
    --cpu 2 `
    --timeout 300 `
    --quiet
