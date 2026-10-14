# pp-switch 服务镜像（control-api / executor / mock CRM 共用）
# 多阶段构建：uv 装依赖 → 精简运行层。
# PR-DEP-14：executor 镜像运行时不携带任何出口代理配置（见 compose）。

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_LINK_MODE=copy

# 先装依赖（利用层缓存），再拷源码
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps
RUN uv sync --all-packages --no-dev --frozen

FROM python:3.12-slim-bookworm
WORKDIR /app
# 运行层只带虚拟环境与源码
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PP_SWITCH_DB_URL=sqlite:////data/pp-switch.db \
    PP_SWITCH_AUTO_MIGRATE=true
# 非 root 运行
RUN useradd -m -u 10001 ppswitch && mkdir -p /data && chown ppswitch /data
USER ppswitch
VOLUME ["/data"]
EXPOSE 8911

# 默认启动 control-api；executor 服务在 compose 里覆盖 command
CMD ["uvicorn", "ppswitch_control.app:create_app_from_env", "--factory", \
     "--host", "0.0.0.0", "--port", "8911"]
