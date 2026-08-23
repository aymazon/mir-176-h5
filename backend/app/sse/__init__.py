"""SSE 通道内聚模块：ring buffer / 重放计划（todo 5）与会话状态注册表。

事件 id 契约（与前端 todo 6 logStore 协同）：
- 每会话单调递增 int 序号，从 1 起；
- Last-Event-ID 重放按字符串数字解析，非法值视同全新连接；
- buffer 已滚出（Last-Event-ID 过旧）→ 重放流首条插 ``event: gap``（不带 id），
  并重置会话 epoch——其后事件 id 从 1 重新递增；前端收到 gap 先清水位线再插分隔行。
"""
