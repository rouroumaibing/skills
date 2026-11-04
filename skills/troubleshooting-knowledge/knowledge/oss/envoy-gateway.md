---
schema: 1
domain: envoy-gateway
scope: oss
components: [envoy-gateway, envoy-proxy, gateway-api]
symptoms: [网关不通, 路由不生效, "503", "403", 地址未分配]
error_codes: ["503", "403", "502"]
versions: ["1.x"]
aliases: [envoygateway, envoy gateway, eg, 网关]
status: active
confidence: doc-sourced
evidence:
  - run: Gateway API v1.x 规范（gateway.networking.k8s.io，访问于 2026-11-04）
    what: 资源匹配字段与 conditions 语义（parentRefs/hostnames/backendRef/Accepted/Programmed）
    covers: [正常链路, "逐环节排查 ②③"]
  - run: Envoy Gateway v1.x 官方文档（envoyproxy.github.io/envoy-gateway，访问于 2026-11-04）
    what: 数据面 pod 命名、EnvoyProxy CRD、controllerName 取值、egctl translate 存在性
    covers: [正常链路, "逐环节排查 ②", 配置语义]
  - run: Envoy v1.x 官方文档（访问日志 response flags / HTTP 响应语义）
    what: UH=no healthy upstream、UAEX=RBAC 授权拒绝；503/502 归因
    covers: ["逐环节排查 ④⑤", 异常归因速查]
updated: 2026-11-04
source: Envoy Gateway 官方文档 + Gateway API 规范（社区通用事实，随 skill 分发）
---

# Envoy Gateway 排查参考

## 正常链路（请求怎么走）
- **资源匹配关系**（对象之间靠这些字段相连）：
  - `GatewayClass.spec.controllerName` = `gateway.envoyproxy.io/gatewayclass-controller` → 被 envoy-gateway 控制面接管；
  - HTTPRoute 的 `spec.parentRefs[].group/kind/name/namespace` → 对应 Gateway 对象（缺 namespace = 同 namespace）；绑定成功后 Gateway 的 `status.conditions` 出现 `Accepted: True`；
  - Gateway listener 的 `hostname` ↔ HTTPRoute 的 `spec.hostnames`：取交集才可服务（listener hostname 为空 = 接受任意 host）；
  - HTTPRoute 的 `spec.rules[].backendRef.name/namespace/port` → 对应 Service（跨 namespace 需 ReferenceGrant）→ Service 的 endpoints（就绪 pod）。
- **流量走向**：客户端 → Gateway `status.addresses`（前置 LoadBalancer/NodePort Service 地址）→ 数据面 Envoy pod（命名 `envoy-<namespace>-<gateway名>-<hash>-<hash>`，按 EnvoyProxy 副本数创建）→ 按 hostnames + path/header match 选中 HTTPRoute 规则 → 转发到 backendRef 对应的 endpoints。
- **正常表现**：`Accepted` 与 `Programmed` 均 True、`status.addresses` 非空、endpoints 非空、响应为后端真实回复。

## 逐环节排查
- **① 地址分配**：正常 = `status.addresses` 有值；异常 = 空 → LoadBalancer pending 或 `spec.addresses` 冲突。证据位：Gateway 的 `status`。
- **② 控制面接纳与下发**：正常 = `Accepted: True` + `Programmed: True`（xDS 下发就绪）；异常 = `Accepted: False`（reason 指明 parentRefs 绑定失败/跨 ns 权限不足）或 `Programmed: False`。证据位：`status.conditions` 及其 reason；`egctl x translate` 把 Gateway API 资源翻译成 Envoy 实际 xDS 配置，区分「没下发」与「下发了不生效」（子命令随版本演进，以 `egctl --help` 为准）。
- **③ 路由匹配**：正常 = 请求 host 落在 hostnames 交集且 match 命中某条 rule；异常 = host 交集为空或 match 全不命中 → Envoy 默认响应（404）〔推论：默认响应码未在本文档逐条核对，待运行时复核〕。证据位：HTTPRoute 的 spec + 访问日志。
- **④ 上游选择**：正常 = backendRef 对应 Service 的 endpoints 非空；异常 = endpoints 空（后端未就绪/探针全挂）→ 无健康上游。证据位：Service 的 endpoints 列表。
- **⑤ 后端响应**：正常 = 应用真实响应返回；异常 = 连接被拒/非法响应，或 Envoy 侧授权拒绝。证据位：访问日志 response flags + 后端 pod 日志。

## 异常归因速查
| 环节 | 现象 | 归因类别 | 证据位 |
|---|---|---|---|
| ① 地址分配 | 地址未分配 | LoadBalancer pending / Addresses 配置冲突 | `status.addresses` |
| ② 控制面下发 | 路由不生效（Accepted False） | parentRefs 未绑定 / ReferenceGrant 缺失 | `status.conditions` 的 reason |
| ③ 路由匹配 | 路由不生效（host 交集空） | hostnames 无交集 / match 全不命中 | HTTPRoute spec + 访问日志 |
| ④ 上游选择 | "503" | 无健康上游（endpoints 空） | 访问日志 `UH`；endpoints 为空 |
| ⑤ 后端响应 | "502" | 上游连接失败/返回非法响应 | 访问日志 flags；后端 pod 日志 |
| ⑤ 后端响应 | "403" | Envoy 侧授权拒绝（RBAC/SecurityPolicy），与应用自身 403 是两回事 | 访问日志 `UAEX`；SecurityPolicy 配置 |

## 配置语义（客观事实）
- `GatewayClass.spec.controllerName` 决定由哪个实现接管；Envoy Gateway 只处理 controllerName 为 `gateway.envoyproxy.io/gatewayclass-controller` 的 GatewayClass。
- EnvoyProxy CRD（`envoyproxies.gateway.envoyproxy.io`）调整数据面默认行为（副本数、日志、访问日志格式等）；未显式指定 targetRef 时走 class 级默认。

## 边界与置信度说明
- `confidence: doc-sourced`（依据为 v1.x 文档，见 evidence）；与运行时取证冲突时以运行时为准，走知识冲突报告回流。

### 待运行时复核（有集群环境时逐条核对，通过后升 verified）
- HTTPRoute 绑定失败各 reason 原文（NoMatchingParent / NotAllowedByListeners / NoMatchingListenerHostname / RefNotPermitted / BackendNotFound）；
- 路由未被接纳时请求是否确实落入 404（本条目标为 〔推论〕）；
- SecurityPolicy 拒绝时 `response_code_details` 实际取值；
- Envoy 代理 Service 端口与容器监听端口的映射规则；数据面 Deployment/Service 标签与 pod 命名规则；
- `egctl x status`、`egctl x translate` 在部署版本是否可用；访问日志默认是否开启、默认字段。
