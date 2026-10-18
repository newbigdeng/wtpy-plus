# WT Plus 控制台

网页管理监控、调度和回测。
前端基于 WonderTrader 的 wtconsole。
原有 MIT 许可保留在 [LICENSE](LICENSE)。

技术栈见 [package.json](package.json)。
依赖版本固定在 package-lock.json。

## 怎样运行开发服务？

安装依赖，再启动开发服务器。

需要 Node.js 22.12 或更高版本。
在 wtpy-plus 仓库根目录执行：

```sh
cd console
npm ci
npm run dev
```

Vite 会转发接口到本机的 8100 端口。
实际服务和账户由 Python 后端管理。

## 怎样生成发布页面？

构建产物放在 dist 目录。

```sh
npm run build
```

推荐在仓库根目录重建工作台镜像：

```sh
docker compose up -d --build
```

Docker 提供页面的目录是：
`/opt/wt/static/console`。

若只替换前端，在仓库根目录执行：

```sh
docker compose cp console/dist/. workbench:/tmp/wt-console-dist/
docker compose exec -u 0 workbench sh -c 'mkdir -p /opt/wt/static/console && cp -a /tmp/wt-console-dist/. /opt/wt/static/console/'
```

复制操作在容器内以 root 执行。
业务数据仍保存在原数据卷中。

## 怎样检查网页交互？

测试使用合成数据和模拟接口。

不需要交易账户，也不读取账户文件。
首次使用时安装测试浏览器：

```sh
npx playwright install --with-deps chromium
npm test
```

命令会自动启动本地开发服务器。
三项测试覆盖日志、编辑器和页面导航。
结果只写入被 Git 忽略的 test-results。
