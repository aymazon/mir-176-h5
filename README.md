# mir-176-h5

传奇 1.76 H5 网页游戏 MVP（竖屏 H5 挂机）。

A vertical-screen, idle (auto-battle) web game MVP based on Legend of Mir 1.76. Frontend is React + TypeScript + Vite + PIXI; backend is FastAPI with a WebSocket gateway, a server-side world/game loop, and PostgreSQL persistence.

## 开发入口

开发入口见 [docs/MVP/MVP-DESIGN.md](docs/MVP/MVP-DESIGN.md) 10.2（本地开发环境：建库、后端、前端启动步骤与 `.env.example`）。

## 顶层目录结构

```
mir-176-h5/
├─ frontend/                 # React + TS + Vite + PIXI
│  └─ src/                   # api / net / store / game / ui
├─ backend/
│  ├─ app/                   # FastAPI: api / net / world / autobattle / db / content
│  ├─ game_data/             # 数值表 JSON（种子源）
│  └─ tests/
├─ deploy/                   # nginx conf、systemd unit、docker-compose(dev)
├─ scripts/                  # 时间压缩验收脚本、压测脚本
└─ docs/MVP/MVP-DESIGN.md    # 设计文档
```

完整目录建议见设计文档附录 B。
