# Packages ONLY the MCP Relay (server.py) - it is COM-free and holds no
# password, so it's the only half of this project that CAN be containerized.
# The Host Agent must always run natively on Windows (it needs the host's
# interactive session for COM automation) - see README.md / plan doc for why.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

# Override at `docker run` time to point at your actual Host Agent and
# desired tool tier - see README.md.
ENV NOTES_MCP_PROFILE=read
ENV NOTES_HOST_AGENT_URL=http://host.docker.internal:8765

ENTRYPOINT ["python", "-m", "notes_mcp.server"]
