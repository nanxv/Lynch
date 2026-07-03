# 铁律监控 iOS App

## 前置条件

- Node.js 18+（推荐用 [nodejs.org](https://nodejs.org) 或 `brew install node`）
- Xcode + iOS 模拟器（或 iPhone 真机 + Expo Go）
- 后端 API 已启动：`~/Projects/stock-monitor/scripts/run_api.sh`

## 第一次启动

```bash
cd ~/Projects/stock-monitor/mobile
cp .env.example .env
npm install
npm run ios
```

## API 地址配置

| 场景 | `.env` 设置 |
|------|-------------|
| iOS 模拟器 | `EXPO_PUBLIC_API_URL=http://127.0.0.1:8000` |
| iPhone 真机 | `EXPO_PUBLIC_API_URL=http://<你Mac的局域网IP>:8000` |

查 Mac IP：系统设置 → 网络，或终端 `ipconfig getifaddr en0`

## 三个 Tab

1. **狙击雷达** — 红/黄/绿状态 + 周线 D 值
2. **股票池** — 增删股票，实时同步后端
3. **信号日志** — 历史买入信号时间轴

## 推送通知

- **模拟器**：不支持 Push，可正常看雷达数据
- **真机**：首次打开会请求通知权限，token 自动注册到后端

真机 Push 需配置 EAS（可选，Phase 3）：

```bash
npm install -g eas-cli
eas init
```

## 常见问题

**雷达页显示「无法连接后端」**  
→ 确认 `./scripts/run_api.sh` 在跑，且 API 地址正确。

**真机连不上 127.0.0.1**  
→ 127.0.0.1 是手机自己，必须改成 Mac 的局域网 IP。
