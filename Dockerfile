# Optional container image. Most people should use `uvx life-admin-mcp` instead.
# Run:  docker run -i --rm -v life-admin:/data life-admin-mcp
FROM python:3.12-slim
LABEL io.modelcontextprotocol.server.name="io.github.bishalkarki01/life-admin-mcp"

RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && mkdir /data && chown app:app /data

USER app
ENV LIFE_ADMIN_DB=/data/life_admin.db
VOLUME ["/data"]
ENTRYPOINT ["life-admin-mcp"]
