param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "restart", "logs", "shell", "open", "status", "help")]
    [string]$Action = "help"
)

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ContainerName = "homeassistant"
$WebUrl = "http://localhost:8123"

function Show-Help {
    Write-Host ""
    Write-Host "Home Assistant Docker Helper" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Usage:"
    Write-Host "  .\ha.ps1 start      启动 Home Assistant"
    Write-Host "  .\ha.ps1 stop       停止 Home Assistant"
    Write-Host "  .\ha.ps1 restart    重启 Home Assistant"
    Write-Host "  .\ha.ps1 logs       查看日志（实时）"
    Write-Host "  .\ha.ps1 shell      进入容器命令行"
    Write-Host "  .\ha.ps1 open       打开 Web 页面"
    Write-Host "  .\ha.ps1 status     查看容器状态"
    Write-Host ""
}

function Go-ProjectDir {
    Set-Location $ProjectDir
}

function Start-HA {
    Go-ProjectDir
    Write-Host "==> 启动 Home Assistant..." -ForegroundColor Green
    docker compose up -d
}

function Stop-HA {
    Go-ProjectDir
    Write-Host "==> 停止 Home Assistant..." -ForegroundColor Yellow
    docker compose down
}

function Restart-HA {
    Go-ProjectDir
    Write-Host "==> 重启 Home Assistant..." -ForegroundColor Yellow
    docker compose restart homeassistant
}

function Show-Logs {
    Write-Host "==> 查看日志（Ctrl+C 退出）" -ForegroundColor Cyan
    docker logs -f $ContainerName
}

function Enter-Shell {
    Write-Host "==> 进入容器命令行" -ForegroundColor Cyan
    docker exec -it $ContainerName bash
}

function Open-Web {
    Write-Host "==> 打开 Home Assistant Web 页面" -ForegroundColor Cyan
    Start-Process $WebUrl
}

function Show-Status {
    docker ps --filter "name=$ContainerName"
}

switch ($Action) {
    "start"   { Start-HA }
    "stop"    { Stop-HA }
    "restart" { Restart-HA }
    "logs"    { Show-Logs }
    "shell"   { Enter-Shell }
    "open"    { Open-Web }
    "status"  { Show-Status }
    "help"    { Show-Help }
    default   { Show-Help }
}
