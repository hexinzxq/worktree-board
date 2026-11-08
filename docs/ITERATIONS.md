# 迭代记录

每周一个项目，每天最多五次迭代，每次一条：改了什么、为什么、验证结果。

## 2026-11-07

### #1 scaffold：只读看板跑起来

- **改了什么**：`scan.py`（git worktree porcelain → dataclass）、`server.py`（stdlib HTTP：`/api/state` + 单页）、`cli.py`（`wtree ls` / `wtree serve`）、`static/index.html`、10 个测试、CI。
- **为什么**：本周项目选定 [max-sixty/worktrunk](https://github.com/max-sixty/worktrunk) 的差异化角度——它管 git 本身，我们加一块人看的板和会话状态。先让「数据 → 页面」这条最短链路通，再谈会话关联。
- **验证**：`python -m unittest discover -s tests -t .` 10 个测试通过；`wtree ls` 在临时仓库上跑通；CI 绿。

## 2026-11-08

### #1 终端里也能盯着看：`wtree ls --watch`

- **改了什么**：`cli.py` 新增 `--watch` 与 `--interval`（默认 1.0 秒），`_watch()` 每帧先 `\033[2J\033[H` 清屏再重画同一张表；`--watch --json` 与 `--interval <= 0` 直接报错退出 2，不静默。新增 `tests/test_cli.py` 4 个用例，其中一个真起子进程数清屏序列。
- **为什么**：第一版只有 `ls` 一次性输出和 `serve` 网页两条路。实际用的时候我是拿它盯一个仓库的：脚本跑一下、看一眼脏没脏，来回敲 `wtree ls` 很蠢，开浏览器更重；放在本来就开着的终端里才对。`--watch` 只读、只重画，不引入任何状态。
- **验证**：14 个测试全过（新用例断言 0.6 秒内至少画两帧、表头与 `main *` 都在）；本机 `wtree ls -C . --watch --interval 0.1` 起停正常，ctrl-c 退出码 0；CI 3.10/3.12 绿。
