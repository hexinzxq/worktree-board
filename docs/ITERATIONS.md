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

### #2 目录都没了的 worktree，表里看着像健康的

- **改了什么**：`report()["totals"]` 增加 `prunable` / `locked` 计数；`_table()` 给行加 `(prunable)` / `(locked)` 标记，表尾多一行提示 `git worktree prune`；页面状态列加「已不存在」「锁定」徽章，顶部 chips 多一格「N 个已不存在」。新增 3 个断言（scan 认得上、表格说出来、API 计数在）。
- **为什么**：`git worktree list` 对目录已经被删掉的 worktree 仍然列着，porcelain 里带 `prunable`。原来的表把它画成 `dirty=-`、落后 `-` 的行，跟一个干净的正常 worktree 几乎一样——看板最不该干的就是「有问题的东西看起来没问题」。这次的输入是临时仓库里 `worktree add` 之后把目录删掉，两行输出对比很直白。
- **验证**：16 个测试全过；真仓库复跑，`gone (prunable)` 行 + 表尾 note 都在；页面 totals 多了 `prunable: 0` 字段（老的 API 消费方照常读前三个键）。推送仍受网络影响，见当天汇报。

### #3 端口被占的时候，它选择甩一个 traceback

- **改了什么**：`cli.py` 的 `serve` 分支把 `serve()` 包进 `try/except OSError`：端口占用（`EADDRINUSE`）或绑定失败时打 `error: cannot listen on 127.0.0.1:8765 — Address already in use. Pass --port to pick another.`，退出 2，不再输出栈。新增 1 个用例（先占一个端口再跑 `serve --port`，断言退出码 2、有提示、没有 `Traceback`）。
- **为什么**：8765 是个常用端口，第二次 `wtree serve` 撞上它是这个工具最正常的意外。原来的行为是 `OSError` 一路冒到顶层，退出码 1，用户看到十几行栈才反应过来「哦，原来已经开着了」。错误信息应该先说人话。
- **验证**：17 个测试全过；手复现：先占端口再起，现在是一行提示 + exit 2。推送到远程仍受网络影响，见当天汇报。
