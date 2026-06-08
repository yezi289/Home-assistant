# 自动附加 USB 设备到 WSL
$BUSID = "6-2"  # 修改为你的设备 BUSID

Write-Host "正在附加设备 $BUSID 到 WSL..."
usbipd attach --wsl --busid $BUSID

if ($LASTEXITCODE -eq 0) {
    Write-Host "设备附加成功！" -ForegroundColor Green
} else {
    Write-Host "设备附加失败，请检查设备是否已绑定或被占用。" -ForegroundColor Red
}