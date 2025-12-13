#!/bin/bash

# 获取脚本所在目录
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WEB_URL="http://localhost:8123"
ACTION="${1:-help}"

show_help() {
    echo ""
    echo "Home Assistant Docker Helper"
    echo ""
    echo "Usage:"
    echo "  ./ha.sh start      启动 Home Assistant"
    echo "  ./ha.sh stop       停止 Home Assistant"
    echo "  ./ha.sh restart    重启 Home Assistant"
    echo "  ./ha.sh logs       查看日志（实时）"
    echo "  ./ha.sh shell      进入容器命令行"
    echo "  ./ha.sh open       显示 Web 页面地址"
    echo "  ./ha.sh status     查看容器状态"
    echo ""
}

go_project_dir() {
    cd "$PROJECT_DIR" || exit 1
}

start_ha() {
    go_project_dir
    echo "==> 启动 Home Assistant..."
    docker compose up -d
}

stop_ha() {
    go_project_dir
    echo "==> 停止 Home Assistant..."
    docker compose down
}

restart_ha() {
    go_project_dir
    echo "==> 重启 Home Assistant..."
    docker compose restart
}

show_logs() {
    go_project_dir
    echo "==> 查看日志（Ctrl+C 退出）"
    docker compose logs -f
}

enter_shell() {
    go_project_dir
    echo "==> 进入容器命令行"
    docker compose exec homeassistant bash
}

open_web() {
    echo ""
    echo "Home Assistant Web 地址："
    echo "$WEB_URL"
    echo ""
}

show_status() {
    go_project_dir
    docker compose ps
}

case "$ACTION" in
    start)   start_ha ;;
    stop)    stop_ha ;;
    restart) restart_ha ;;
    logs)    show_logs ;;
    shell)   enter_shell ;;
    open)    open_web ;;
    status)  show_status ;;
    *)       show_help ;;
esac
