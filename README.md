# 今日波浪 Dashboard — GitHub 实时发布

每 5 分钟自动重新生成 `index.html` 并发布到 GitHub Pages，
手机/分享链接打开就是最新数据，可交互（悬停/缩放/点圈）。

## 你要做的（约 10 分钟，一次性）

### 1. 建 repo
- GitHub 新建仓库，名字如 `spy-wave`，选 **Public**（免费 Pages 要求公开仓库）
- 不用勾选 README/.gitignore（下面会直接 push）

### 2. 放 API 密钥
- 进 repo → **Settings → Secrets and variables → Actions → New repository secret**
- 加两个（名字必须完全一致）：
  - `ALPACA_KEY` = 你的 Alpaca paper key
  - `ALPACA_SECRET` = 你的 Alpaca paper secret
- 只做诊断图，不下单（`TRADING_ENABLED` 默认 false，脚本里也没有下单逻辑）

### 3. Push 这些文件
```bash
cd wave-deploy
git init
git add -A
git commit -m "wave dashboard"
git branch -M main
git remote add origin https://github.com/你的用户名/spy-wave.git
git push -u origin main
```

### 4. 开 Pages
- repo → **Settings → Pages → Source** 选 `Deploy from a branch`
- Branch 选 `gh-pages` / `root`，Save
- 等 1-2 分钟，打开 https://你的用户名.github.io/spy-wave/ 
- 也可以在 Actions 页点 `wave-dashboard` → `Run workflow` 立刻跑一次

### 5. 分享
把上面的链接发给别人就行。页面每 3 分钟自动刷新（`WAVE_AUTOREFRESH=180`），
数据每 5 分钟更新（Actions cron 最小粒度）。

## 定时任务说明
`.github/workflows/wave.yml`：
- `schedule: '*/5 * * * *'` 每 5 分钟跑
- 跑 `python gen_wave_today.py`，输出 `public/index.html`
- `peaceiris/actions-gh-pages` 推到 `gh-pages` 分支发布

## 本地测试
```bash
ALPACA_KEY=xxx ALPACA_SECRET=yyy WAVE_OUT_DIR=public WAVE_OUT_NAME=index.html WAVE_AUTOREFRESH=180 python gen_wave_today.py
```
