#!/usr/bin/env python3
"""
简易Web服务器 — 为作战面板提供HTTP服务
支持静态文件和服务端推送

用法：
  python3 scripts/server.py              # 启动 (端口8080)
  python3 scripts/server.py --port 80    # 自定义端口
  python3 scripts/server.py --stop       # 停止
"""
import os, sys, signal, subprocess, time

BASE = os.path.expanduser("~/V2board")
PID_FILE = os.path.join(BASE, "server.pid")
PORT = 8080

def start(port=PORT):
    # 检查是否已在运行
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            pid = f.read().strip()
        try:
            os.kill(int(pid), 0)
            print(f"✅ 服务器已在运行 (PID:{pid}, 端口:{port})")
            return
        except:
            os.remove(PID_FILE)
    
    # 启动HTTP服务器
    cmd = f"cd {BASE} && nohup python3 -m http.server {port} > /dev/null 2>&1 & echo $!"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5, executable='/bin/bash')
    pid = result.stdout.strip()
    
    if pid and pid.isdigit():
        with open(PID_FILE, 'w') as f:
            f.write(pid)
        print(f"✅ 作战面板已启动: http://localhost:{port}/dashboard.html")
        print(f"   PID: {pid}")
    else:
        print(f"❌ 启动失败: {result.stderr}")

def stop():
    if not os.path.exists(PID_FILE):
        print("❌ 服务器未运行")
        return
    with open(PID_FILE) as f:
        pid = f.read().strip()
    try:
        os.kill(int(pid), signal.SIGTERM)
        os.remove(PID_FILE)
        print(f"✅ 服务器已停止 (PID:{pid})")
    except:
        os.remove(PID_FILE)
        print("❌ 停止失败，已清理PID文件")

if __name__ == '__main__':
    args = sys.argv[1:]
    if '--stop' in args:
        stop()
    elif '--port' in args:
        idx = args.index('--port')
        port = int(args[idx+1]) if idx+1 < len(args) else PORT
        start(port)
    else:
        start()
