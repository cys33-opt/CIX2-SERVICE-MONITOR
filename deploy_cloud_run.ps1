# Google Cloud Run 원클릭 배포 스크립트
param(
    [string]$ProjectId = "",
    [string]$ServiceName = "hmm-monitor",
    [string]$Region = "asia-northeast3" # 서울 리전
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    Write-Host "gcloud CLI가 설치되어 있지 않습니다. 먼저 'winget install Google.CloudSDK' 로 설치해 주세요." -ForegroundColor Yellow
    exit 1
}

if ([string]::IsNullOrWhiteSpace($ProjectId)) {
    $ProjectId = (gcloud config get-value project 2>$null)
}
if ([string]::IsNullOrWhiteSpace($ProjectId) -or $ProjectId -eq "(unset)") {
    $ProjectId = Read-Host "Google Cloud 프로젝트 ID를 입력하세요"
    gcloud config set project $ProjectId
}

Write-Host "[$ProjectId] 프로젝트의 Cloud Run ($Region) 에 '$ServiceName' 배포를 시작합니다..." -ForegroundColor Cyan

gcloud run deploy $ServiceName `
    --source . `
    --region $Region `
    --allow-unauthenticated `
    --memory 1Gi `
    --timeout 300

Write-Host "배포가 완료되었습니다!" -ForegroundColor Green
