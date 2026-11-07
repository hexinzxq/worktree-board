# 迭代记录

每周一个项目，每天最多五次迭代，每次一条：改了什么、为什么、验证结果。

## 2026-11-07

### #1 scaffold：只读看板跑起来

- **改了什么**：`scan.py`（git worktree porcelain → dataclass）、`server.py`（stdlib HTTP：`/api/state` + 单页）、`cli.py`（`wtree ls` / `wtree serve`）、`static/index.html`、10 个测试、CI。
- **为什么**：本周项目选定 [max-sixty/worktrunk](https://github.com/max-sixty/worktrunk) 的差异化角度——它管 git 本身，我们加一块人看的板和会话状态。先让「数据 → 页面」这条最短链路通，再谈会话关联。
- **验证**：`python -m unittest discover -s tests -t .` 10 个测试通过；`wtree ls` 在临时仓库上跑通；CI 绿。
