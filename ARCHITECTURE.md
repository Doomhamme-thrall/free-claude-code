# Free Claude Code — 架构说明文档

本文档全面描述 **Free Claude Code** 的功能、对外暴露的 API、每项功能的具体实现方式，以及请求在各模块之间的数据流向。

---

## 一、项目概述

**Free Claude Code** 是一个基于 [FastAPI](https://fastapi.tiangolo.com/) 的 ASGI 代理服务器。它实现了完整的 [Anthropic Messages API](https://docs.anthropic.com/en/api/messages) 接口，使 Claude Code CLI、VS Code 插件、JetBrains ACP 等客户端无需修改即可透明地将请求转发到：

- **NVIDIA NIM**（OpenAI `/chat/completions` 格式，通过转换适配）
- **OpenRouter**、**DeepSeek**、**LM Studio**、**llama.cpp**、**Ollama**（Anthropic Messages 原生格式透传）
- **自定义**（Custom，OpenAI 兼容格式）

同时提供可选的 **Discord / Telegram 机器人**包装器，以及语音笔记转录（Whisper / NVIDIA NIM）。

---

## 二、目录结构与模块职责

```
free-claude-code/
├── server.py               # ASGI 入口，调用 api.app.create_asgi_app()
├── api/                    # HTTP 路由、服务层、模型路由、请求优化
│   ├── app.py              # FastAPI 工厂与生命周期管理
│   ├── routes.py           # 路由注册（/v1/messages、/v1/models 等）
│   ├── services.py         # ClaudeProxyService：核心请求编排
│   ├── model_router.py     # ModelRouter：Claude 模型名称 → provider/model 解析
│   ├── optimization_handlers.py  # 本地快速响应（quota probe、prefix、title…）
│   ├── detection.py        # 请求模式识别（探针、配额、标题生成等）
│   ├── gateway_model_ids.py # 网关模型 ID 编解码（供 /v1/models 使用）
│   ├── dependencies.py     # FastAPI 依赖注入（settings、auth、provider 获取）
│   ├── runtime.py          # AppRuntime：应用启动/停机与资源生命周期
│   ├── web_server_tools.py # Web Server Tool 支持（出站 HTTP 代理工具）
│   └── models/             # Pydantic 请求/响应模型
│       ├── anthropic.py    # MessagesRequest、TokenCountRequest 等
│       └── responses.py    # MessagesResponse、ModelResponse 等
├── core/                   # 协议工具（不依赖任何具体 provider）
│   └── anthropic/
│       ├── sse.py          # SSEBuilder：Anthropic SSE 事件构造器
│       ├── conversion.py   # Anthropic → OpenAI 请求格式转换
│       ├── native_messages_request.py  # 原生 Anthropic 请求体构造
│       ├── thinking.py     # thinking/reasoning block 处理
│       ├── tokens.py       # 本地 token 估算（tiktoken）
│       ├── tools.py        # 工具调用解析辅助
│       ├── content.py      # 消息内容块提取
│       └── stream_contracts.py  # SSE 流契约断言（用于测试）
├── providers/              # 各后端 provider 实现
│   ├── base.py             # BaseProvider 抽象类、ProviderConfig
│   ├── registry.py         # ProviderRegistry：provider 缓存、模型发现、验证
│   ├── openai_compat.py    # OpenAIChatTransport（NIM 等 OpenAI 格式）
│   ├── anthropic_messages.py  # AnthropicMessagesTransport（原生 Anthropic 格式）
│   ├── nvidia_nim/         # NVIDIA NIM 具体实现（OpenAI 格式转换）
│   ├── open_router/        # OpenRouter（Anthropic Messages 透传）
│   ├── deepseek/           # DeepSeek（Anthropic Messages 透传）
│   ├── lmstudio/           # LM Studio（Anthropic Messages 透传）
│   ├── llamacpp/           # llama.cpp（Anthropic Messages 透传）
│   ├── ollama/             # Ollama（Anthropic Messages 透传）
│   ├── custom/             # 自定义 OpenAI 兼容 provider
│   ├── error_mapping.py    # provider 错误 → Anthropic 错误格式映射
│   ├── rate_limit.py       # GlobalRateLimiter：速率限制与并发控制
│   └── model_listing.py    # 模型列表解析工具
├── config/                 # 配置与常量
│   ├── settings.py         # Settings（pydantic-settings，读取 .env）
│   ├── provider_catalog.py # PROVIDER_CATALOG：provider 元数据（无实现依赖）
│   ├── provider_ids.py     # SUPPORTED_PROVIDER_IDS
│   ├── nim.py              # NimSettings（NIM 专属参数）
│   ├── constants.py        # 全局常量（超时、token 上限等）
│   └── logging_config.py   # loguru 日志配置
├── messaging/              # Discord / Telegram 机器人（可选）
│   ├── handler.py          # ClaudeMessageHandler：平台无关的消息处理核心
│   ├── session.py          # SessionStore：JSON 持久化会话/树状态
│   ├── trees/              # 树状消息队列（支持回复分支）
│   ├── platforms/          # Discord / Telegram 平台适配器
│   ├── commands.py         # /stop、/clear、/stats 命令实现
│   ├── transcription.py    # Whisper 语音转文字
│   └── voice.py            # 语音笔记入口
├── cli/                    # Claude CLI 子进程管理
│   ├── manager.py          # CLISessionManager：多实例 CLI 会话池
│   ├── session.py          # CLISession：单个 Claude CLI 进程生命周期
│   ├── process_registry.py # 全局进程注册表（用于清理）
│   └── entrypoints.py      # 包命令入口（free-claude-code、fcc-init）
├── smoke/                  # 端到端冒烟测试（FCC_LIVE_SMOKE=1 启用）
└── tests/                  # 单元与契约测试
```

---

## 三、暴露的 API

代理以 FastAPI 实现，监听默认端口 **8082**。所有需要认证的路由均通过 `Authorization: Bearer <token>` 或 `x-api-key: <token>` 头与 `ANTHROPIC_AUTH_TOKEN` 比对（留空则跳过认证）。

### 3.1 消息接口

| 方法 | 路径 | 功能 |
|------|------|------|
| `POST` | `/v1/messages` | 创建消息（始终以 SSE 流返回） |
| `HEAD / OPTIONS` | `/v1/messages` | 兼容性探针，返回 204 |
| `POST` | `/v1/messages/count_tokens` | 统计请求 token 数，返回 `{"input_tokens": N}` |
| `HEAD / OPTIONS` | `/v1/messages/count_tokens` | 兼容性探针，返回 204 |

**`POST /v1/messages` 请求体（`MessagesRequest`）**

```json
{
  "model": "claude-3-5-sonnet-20241022",
  "messages": [{"role": "user", "content": "Hello"}],
  "max_tokens": 1024,
  "system": "You are a helpful assistant.",
  "tools": [],
  "thinking": {"type": "enabled", "budget_tokens": 5000},
  "stream": true
}
```

**SSE 响应事件序列（Anthropic 格式）**

```
event: message_start
event: content_block_start
event: content_block_delta  (text_delta / thinking_delta / input_json_delta)
event: content_block_stop
event: message_delta        (含 stop_reason 和 output_tokens)
event: message_stop
```

### 3.2 模型列表

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/v1/models` | 返回代理广告的所有可选模型 |

**响应示例**

```json
{
  "data": [
    {"id": "fcc:nvidia_nim/z-ai/glm4.7", "display_name": "nvidia_nim/z-ai/glm4.7", "created_at": "..."},
    {"id": "fcc-nothink:nvidia_nim/z-ai/glm4.7", "display_name": "nvidia_nim/z-ai/glm4.7 (no thinking)", "created_at": "..."},
    {"id": "claude-3-5-sonnet-20241022", "display_name": "Claude 3.5 Sonnet", "created_at": "..."}
  ],
  "has_more": false
}
```

每个配置的 provider 模型会同时出现两个变体：
- `fcc:<provider>/<model>`：默认（支持 thinking）
- `fcc-nothink:<provider>/<model>`：禁用 thinking

### 3.3 管理接口

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/` | 返回 `{"status":"ok","provider":"…","model":"…"}` |
| `GET` | `/health` | 健康检查，返回 `{"status":"healthy"}` |
| `POST` | `/stop` | 停止所有 CLI 会话 / 消息任务 |
| `HEAD / OPTIONS` | `/`、`/health` | 兼容性探针 |

---

## 四、核心功能与实现方式

### 4.1 应用启动与生命周期

**相关文件：** `server.py` → `api/app.py` → `api/runtime.py`

```
server.py
  └─ create_asgi_app()
       └─ GracefulLifespanApp(create_app(lifespan_enabled=False))
            ├─ lifespan.startup → AppRuntime.startup()
            │    ├─ ProviderRegistry()             # 初始化 provider 缓存
            │    ├─ validate_configured_models()   # 验证 .env 中配置的模型是否在 provider 侧存在
            │    ├─ start_model_list_refresh()     # 后台异步拉取各 provider 模型列表
            │    └─ _start_messaging_if_configured() # 按需启动 Discord/Telegram
            └─ lifespan.shutdown → AppRuntime.shutdown()
                 ├─ messaging_platform.stop()
                 ├─ cli_manager.stop_all()
                 └─ provider_registry.cleanup()
```

`GracefulLifespanApp` 自己处理 ASGI lifespan 事件，在启动失败时向 ASGI 服务器返回清晰的失败消息，而不是 Starlette 的默认堆栈跟踪。

---

### 4.2 请求处理主流程（POST /v1/messages）

**相关文件：** `api/routes.py` → `api/services.py` → `api/model_router.py` → `providers/`

```
客户端 HTTP POST /v1/messages
  │  Body: MessagesRequest（Pydantic 校验）
  │
  ▼
routes.py: create_message()
  └─ ClaudeProxyService.create_message(request_data)
       │
       ├─ 1. _require_non_empty_messages()       # 验证消息非空
       │
       ├─ 2. ModelRouter.resolve_messages_request()
       │       │  输入: request_data.model（如 "claude-3-5-sonnet-20241022"）
       │       │  解析: 查 .env MODEL_OPUS/MODEL_SONNET/MODEL_HAIKU/MODEL
       │       │       或解码 gateway_model_id（fcc: 前缀）
       │       │  输出: RoutedMessagesRequest
       │       │         ├─ .request.model = "z-ai/glm4.7"（provider 侧模型名）
       │       │         └─ .resolved.provider_id = "nvidia_nim"
       │       │               thinking_enabled = True/False
       │
       ├─ 3. Web Server Tool 检测（若 ENABLE_WEB_SERVER_TOOLS=true）
       │       └─ stream_web_server_tool_response() → SSE 流（本地处理，不调用 provider）
       │
       ├─ 4. try_optimizations()                 # 本地快速响应（见 4.3）
       │       └─ 命中则直接返回 MessagesResponse（非流式）
       │
       ├─ 5. provider.preflight_stream()         # 提前构造并校验 upstream 请求体
       │
       ├─ 6. token_counter()                     # tiktoken 估算输入 token 数
       │
       └─ 7. provider.stream_response()          # 调用 upstream → 返回 SSE StreamingResponse
```

**数据变量流向（第 2 步 ModelRouter）**

```
request_data.model: str
  → ModelRouter.resolve(claude_model_name)
      → settings.resolve_model(claude_model_name)   # 从 .env MODEL_OPUS/SONNET/HAIKU/MODEL 取值
      → Settings.parse_provider_type(provider_model_ref)  # 提取 "nvidia_nim"
      → Settings.parse_model_name(provider_model_ref)     # 提取 "z-ai/glm4.7"
  → ResolvedModel { provider_id, provider_model, thinking_enabled }
  → routed.request.model = resolved.provider_model  # 替换模型名
```

---

### 4.3 请求优化（本地快速响应）

**相关文件：** `api/optimization_handlers.py`、`api/detection.py`

当 Claude Code 发出内部探针请求时，代理在本地直接返回响应，节省 provider 配额和延迟：

| 优化项 | 触发条件（detection.py） | 本地响应内容 |
|--------|--------------------------|-------------|
| `try_quota_mock` | 配额检查探针 | `"Quota check passed."` |
| `try_prefix_detection` | `/` 前缀检测请求 | 命令前缀字符串 |
| `try_title_skip` | 标题生成请求 | `"Conversation"` |
| `try_suggestion_skip` | 建议模式请求 | 空字符串 |
| `try_filepath_mock` | 文件路径提取请求 | 解析出的文件路径列表 |

所有优化均可通过 `.env` 中对应的 `enable_*` 开关关闭（如 `FAST_PREFIX_DETECTION=false`）。

**数据流向**

```
request_data: MessagesRequest
  → is_quota_check_request(request_data) → bool
  → if True: _text_response(request_data, "Quota check passed.", …)
      → MessagesResponse { id, model, content, stop_reason, usage }
      → 直接返回，不进入 provider
```

---

### 4.4 模型路由

**相关文件：** `api/model_router.py`、`config/settings.py`、`api/gateway_model_ids.py`

**三种路由路径：**

1. **网关 ID（fcc: 前缀）**：来自 `/v1/models` 的模型 picker 直接选择的模型  
   `fcc:nvidia_nim/z-ai/glm4.7` → `decode_gateway_model_id()` → `(provider_id="nvidia_nim", provider_model="z-ai/glm4.7")`

2. **直接 provider 格式**：用户直接在请求中填写 `provider_id/model_name`  
   `"nvidia_nim/z-ai/glm4.7"` → `provider_id="nvidia_nim"`, `provider_model="z-ai/glm4.7"`

3. **Claude 模型名映射**：通过 `.env` 按 tier 映射  
   `"claude-3-5-sonnet-20241022"` → 匹配 `sonnet` → 读取 `MODEL_SONNET` → 如 `"nvidia_nim/z-ai/glm4.7"`

**thinking 解析**

```
settings.resolve_thinking(provider_model_ref)
  → 检查 ENABLE_SONNET_THINKING / ENABLE_OPUS_THINKING / ENABLE_HAIKU_THINKING
  → 无对应 tier 时回落到 ENABLE_MODEL_THINKING（默认 true）
  → fcc-nothink: 前缀的网关 ID 强制设置 force_thinking_enabled=False
```

---

### 4.5 Provider 层架构

**相关文件：** `providers/base.py`、`providers/openai_compat.py`、`providers/anthropic_messages.py`

代理使用两种传输基类：

#### OpenAIChatTransport（NVIDIA NIM、Custom）

将 Anthropic Messages 请求**转换为** OpenAI `/chat/completions` 格式：

```
MessagesRequest（Anthropic 格式）
  │
  ▼ build_base_request_body()                  # core/anthropic/conversion.py
  │   ├─ 消息历史：content blocks → messages[]
  │   ├─ system prompt → messages[0].role=system
  │   ├─ thinking blocks → reasoning_content 字段
  │   └─ tools → OpenAI function_calling 格式
  │
  ▼ NIM 专属后处理（providers/nvidia_nim/request.py）
  │   ├─ _sanitize_nim_tool_schemas()：移除 boolean JSON Schema 子模式
  │   ├─ max_tokens 上限（nim.max_tokens）
  │   ├─ temperature / top_p / stop / seed 补全
  │   └─ extra_body.chat_template_kwargs.thinking=True（thinking 启用时）
  │
  ▼ AsyncOpenAI.chat.completions.create(stream=True)
  │   上游返回 OpenAI delta 流
  │
  ▼ OpenAIChatTransport._stream_response_impl()
      ├─ ThinkTagParser：解析 <think>...</think> 标签 → thinking_delta
      ├─ HeuristicToolParser：将纯文本中的 JSON 工具调用启发式提取
      ├─ sse.emit_thinking_delta / emit_text_delta / start_tool_block …
      └─ SSEBuilder 输出 Anthropic SSE 事件
```

#### AnthropicMessagesTransport（OpenRouter、DeepSeek、LM Studio、llama.cpp、Ollama）

直接透传 Anthropic Messages 请求，仅做最小化规范化：

```
MessagesRequest（Anthropic 格式）
  │
  ▼ build_base_native_anthropic_request_body()  # core/anthropic/native_messages_request.py
  │   ├─ 移除代理私有字段（extra_body 等）
  │   ├─ 限制 max_tokens（ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS）
  │   └─ thinking block 规范化
  │
  ▼ httpx.AsyncClient.post("/messages", stream=True)
  │   上游直接返回 Anthropic SSE 流
  │
  ▼ AnthropicMessagesTransport._iter_stream_chunks()
      ├─ NativeSseBlockPolicyState.transform_native_sse_block_event()
      │   按策略过滤/归并 thinking block（避免 client 端不兼容）
      └─ 直接 yield SSE 行给客户端
```

---

### 4.6 Provider 注册与模型发现

**相关文件：** `providers/registry.py`、`config/provider_catalog.py`

```
AppRuntime.startup()
  │
  ├─ ProviderRegistry()
  │    _providers: dict[provider_id, BaseProvider]
  │    _model_infos_by_provider: dict[provider_id, dict[model_id, ProviderModelInfo]]
  │
  ├─ validate_configured_models(settings)
  │    对每个 .env 中配置的模型，并发调用 provider.list_model_infos()
  │    检查模型是否存在，否则 raise ServiceUnavailableError 中止启动
  │
  └─ start_model_list_refresh(settings)
       asyncio.create_task(registry.refresh_model_list_cache())
       后台并发拉取所有有凭证的 provider 的模型列表 → cache_model_infos()

GET /v1/models
  └─ _build_models_list_response(settings, provider_registry)
       1. settings.configured_chat_model_refs() → 已配置模型
       2. provider_registry.cached_prefixed_model_infos() → 后台发现的模型
       3. SUPPORTED_CLAUDE_MODELS → 内置 Claude 标准模型 ID
       → 每个 provider 模型生成两个变体（fcc: / fcc-nothink:）
```

---

### 4.7 速率限制与并发控制

**相关文件：** `providers/rate_limit.py`、`core/rate_limit.py`

每个 provider 有独立的 `GlobalRateLimiter` 实例（按 provider 名称作用域）：

```
GlobalRateLimiter（进程级单例，按 provider_name 分区）
  ├─ _semaphore: asyncio.Semaphore(max_concurrency)  # 并发槽
  ├─ _rate_window / _rate_limit                       # 滑动时间窗速率
  └─ execute_with_retry(fn, *args)
       ├─ 遇到 429 / RateLimitError → 等待 retry_after 后重试
       └─ 超出 max_retries → 抛出 RateLimitError

相关环境变量：
  PROVIDER_RATE_LIMIT=1       # 每窗口请求数（0 = 不限）
  PROVIDER_RATE_WINDOW=3      # 窗口秒数
  PROVIDER_MAX_CONCURRENCY=5  # 最大并发连接数
```

---

### 4.8 SSE 构造器（SSEBuilder）

**相关文件：** `core/anthropic/sse.py`

`SSEBuilder` 是 OpenAI → Anthropic SSE 格式转换的核心，管理流内所有块的状态：

```python
SSEBuilder(message_id, model, input_tokens, log_raw_events)
  ├─ .message_start()          → event: message_start
  ├─ .ensure_text_block()      → event: content_block_start (type=text)
  ├─ .emit_text_delta(text)    → event: content_block_delta (text_delta)
  ├─ .ensure_thinking_block()  → event: content_block_start (type=thinking)
  ├─ .emit_thinking_delta(t)   → event: content_block_delta (thinking_delta)
  ├─ .start_tool_block(…)      → event: content_block_start (type=tool_use)
  ├─ .emit_tool_delta(i, args) → event: content_block_delta (input_json_delta)
  ├─ .close_all_blocks()       → event: content_block_stop (for each open block)
  ├─ .message_delta(reason, n) → event: message_delta (stop_reason, usage)
  └─ .message_stop()           → event: message_stop
```

块索引由 `BlockIndexTracker` 管理，保证多个并发工具调用的索引连续且不重复。

---

### 4.9 消息机器人（Discord / Telegram）

**相关文件：** `messaging/handler.py`、`messaging/platforms/`、`messaging/trees/`、`messaging/session.py`

#### 架构概述

```
平台事件（Discord message / Telegram update）
  │
  ▼ MessagingPlatform.on_message() 回调
  │   (discord: DiscordPlatform / telegram: TelegramPlatform)
  │
  ▼ ClaudeMessageHandler.handle_message(IncomingMessage)
       │
       ├─ 命令解析（dispatch_command）
       │   /stop、/clear、/stats → CommandDispatcher
       │
       └─ 普通消息 → TreeQueueManager
            │  新消息: 创建新树根节点
            │  回复: 作为父节点的子节点（形成对话分支）
            │
            ▼ 按树顺序处理 → CLISessionManager.get_or_create_session()
                 │
                 ├─ CLISession.send_message(text)    # 向子进程发送输入
                 │
                 └─ CLI 输出事件循环
                      parse_cli_event() → 解析 Claude CLI JSON 事件
                      process_parsed_cli_event() → 更新 TranscriptBuffer
                      ThrottledTranscriptEditor.update() → 节流编辑平台消息
```

#### 会话持久化

```
SessionStore（sessions.json）
  ├─ _trees: { root_id → tree_data }         # 树状对话结构
  ├─ _node_to_tree: { node_id → root_id }    # 节点 → 树 快速查找
  └─ _message_log: { "platform:chat_id" → [message_id_records] }  # /clear 用
```

写入采用**原子替换**（`tempfile` + `os.replace`）和 **0.5s 防抖定时器**，避免频繁 I/O。

---

### 4.10 CLI 子进程管理

**相关文件：** `cli/manager.py`、`cli/session.py`、`cli/process_registry.py`

```
CLISessionManager
  _sessions: { session_id → CLISession }
  _pending_sessions: { temp_id → CLISession }  # 等待 Claude CLI 输出真实 session_id

CLISession
  ├─ _process: asyncio.subprocess.Process      # claude CLI 子进程
  ├─ send_message(text)                        # 写入 stdin
  ├─ stop()                                    # 发送 SIGTERM / SIGKILL
  └─ 异步读取 stdout → 触发 on_output 回调

process_registry.py
  └─ 进程注册表，服务器退出时 kill_all_best_effort() 清理孤儿进程
```

Claude CLI 子进程启动参数：

```
claude --dangerously-skip-permissions
       --output-format stream-json
       --api-url <proxy_url>/v1
       --allowedDirectory <workspace>
       [--resume <session_id>]
```

---

### 4.11 语音转录

**相关文件：** `messaging/transcription.py`、`messaging/voice.py`

支持三种后端（通过 `WHISPER_DEVICE` 配置）：

| `WHISPER_DEVICE` | 实现 | 依赖 |
|------------------|------|------|
| `cpu` / `cuda` | 本地 `faster-whisper` | `voice_local` extra |
| `nvidia_nim` | NVIDIA NIM 托管 ASR | `voice` extra + `NVIDIA_NIM_API_KEY` |

数据流：

```
平台语音文件 URL / 文件 ID
  → 下载为音频字节
  → TranscriptionService.transcribe(audio_bytes)
      → faster_whisper.WhisperModel.transcribe()
         或 NVIDIA NIM ASR HTTP API
  → 转录文本字符串
  → 作为普通文本消息传入 ClaudeMessageHandler
```

---

## 五、配置参考（数据源）

所有配置通过 `config/settings.py` 中的 `Settings`（pydantic-settings）加载，优先级从低到高：

1. `~/.config/free-claude-code/.env`
2. `.env`（工作目录）
3. `FCC_ENV_FILE` 指定的文件
4. 进程环境变量

关键配置变量（完整列表见 `.env.example`）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MODEL` | — | 默认 provider/model（`nvidia_nim/z-ai/glm4.7`） |
| `MODEL_OPUS/SONNET/HAIKU` | — | 按 tier 覆盖路由 |
| `ENABLE_MODEL_THINKING` | `true` | 全局 thinking 开关 |
| `ANTHROPIC_AUTH_TOKEN` | — | 代理认证 token（空=不认证） |
| `PROVIDER_RATE_LIMIT` | `1` | 每时间窗请求数 |
| `PROVIDER_MAX_CONCURRENCY` | `5` | 最大并发连接 |
| `MESSAGING_PLATFORM` | `discord` | `discord`/`telegram`/`none` |
| `LOG_RAW_API_PAYLOADS` | `false` | 记录完整请求体（含敏感信息） |

---

## 六、依赖方向（模块边界）

```
config ──────────────────────────────────────────┐
  │                                               │
  ▼                                               ▼
core/anthropic ──────────────────────────────> providers
       │                                          │
       │                                          ▼
       └──────────────────────────────────────> api ──> cli
                                                 │
                                                 ▼
                                            messaging
```

**关键约束（由 `tests/contracts/test_import_boundaries.py` 强制执行）：**

- `core/` 不得导入 `api`、`messaging`、`cli`、`providers`、`config`、`smoke`
- `api/` 只可从 `providers` 包导入 `providers.base`、`providers.exceptions`、`providers.registry`
- `messaging/` 不得导入 `api`、`cli`、`smoke`
- `config/provider_catalog.py` 不得包含 provider 实现的 import

---

## 七、扩展指南

### 添加新 Provider

1. 在 `config/provider_catalog.py` 的 `PROVIDER_CATALOG` 中添加 `ProviderDescriptor`
2. 在 `providers/` 下创建子模块，继承 `OpenAIChatTransport`（OpenAI 格式）或 `AnthropicMessagesTransport`（原生 Anthropic 格式）
3. 在 `providers/registry.py` 的 `PROVIDER_FACTORIES` 中注册工厂函数
4. 在 `config/provider_ids.py` 的 `SUPPORTED_PROVIDER_IDS` 中添加 ID

### 添加新消息平台

1. 在 `messaging/platforms/` 下实现 `MessagingPlatform` 接口
2. 在 `messaging/platforms/factory.py` 的工厂函数中注册新平台

---

## 八、构建与测试

```bash
# 格式化
uv run ruff format

# 静态检查
uv run ruff check
uv run ty check

# 单元/契约测试
uv run pytest

# 端到端冒烟测试（需要真实 provider 凭证）
FCC_LIVE_SMOKE=1 uv run pytest smoke/ -n 0

# 启动代理
uv run uvicorn server:app --host 0.0.0.0 --port 8082 --timeout-graceful-shutdown 5
```
