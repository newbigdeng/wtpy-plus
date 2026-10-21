FROM node:22.22.1-slim AS frontend
WORKDIR /console
COPY console/package.json console/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY console/ ./
RUN npm run build

FROM wondertrader-plus:native AS workbench
ARG WITH_MARKET_DATA=1
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/workspace/src/wtpy \
    PATH=/opt/wt-venv/bin:$PATH HOME=/data TZ=Asia/Shanghai
WORKDIR /workspace/src/wtpy
COPY requirements-plus.lock /opt/wt/requirements-plus.lock
COPY requirements-data.txt /opt/wt/requirements-data.txt
RUN python3 -m venv /opt/wt-venv \
    && python -m pip install --no-cache-dir -r /opt/wt/requirements-plus.lock \
    && if [ "$WITH_MARKET_DATA" = "1" ]; then python -m pip install --no-cache-dir -r /opt/wt/requirements-data.txt -c /opt/wt/requirements-plus.lock; fi \
    && python -m pip check
COPY wtpy/ ./wtpy/
COPY LICENSE /opt/wt/wtpy-LICENSE
COPY --from=frontend /console/dist/ /opt/wt/static/console/
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend /usr/local/LICENSE /opt/wt/node-LICENSE
COPY --from=frontend /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY --chown=1000:1000 console/ /workspace/src/wtpy/console/
COPY --from=wondertrader-plus:native /opt/wt/native/ ./wtpy/wrapper/linux/
COPY docker/ /opt/wt/docker/
RUN mkdir -p /data /opt/wt/static/mobile /workspace/artifacts /workspace/run \
    && ln -s /opt/wt/native /workspace/artifacts/plus-native \
    && ln -s /data/plus-datasets.json /workspace/run/plus-datasets.json \
    && ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && chmod -R a+rX /workspace/src/wtpy/wtpy /opt/wt/docker /opt/wt/static \
    && chown 1000:1000 /data
WORKDIR /data
USER 1000:1000
EXPOSE 8100
CMD ["python", "/opt/wt/docker/run_workbench.py"]
