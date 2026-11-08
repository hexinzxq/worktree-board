# worktree-board

一个**只读**的看板：把一个仓库的 `git worktree` 摊开成一张表，一个 worktree 对应一个 agent 会话。

对标 [max-sixty/worktrunk](https://github.com/max-sixty/worktrunk)。它解决的是「把 worktree 管理顺手」——创建、切换、清理，CLI 一把梭。这里的差异只有一句话：

> worktrunk 管 git 本身，我们加一块**人看的板**和**会话状态**。

也就是说：谁在哪个 worktree 上跑、有没有未提交改动、落后远程多少、最后一条提交是什么。页面 5 秒拉一次，不改仓库、不碰 git 状态。

## 长什么样

- `wtree ls` — 表格（或 `--json`）列出全部 worktree：分支、改动数、领先/落后、最后提交；目录已经被删掉的 worktree 标 `(prunable)` 并在表尾提示 `git worktree prune`
- `wtree ls --watch` — 同一个表在终端里原地刷（默认 1 秒），不用开浏览器
- `wtree serve` — 同一份数据起在 `http://127.0.0.1:8765`，一个页面，只读
- 零第三方依赖：标准库 + `git` 命令行，`pip install -e .` 之后就能跑

```
$ wtree ls
BRANCH                       DIRTY  AHEAD  BEHIND  LAST COMMIT                          PATH
main *                       0      -      -       initial import                       /repo/app
feature-login                2      1      0       local work                           /repo/feature-login

2 worktree(s), 1 dirty, 0 behind upstream   (/repo/app)
```

## 快速开始

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
cd /path/to/your/repo
wtree ls
wtree serve            # 打开 http://127.0.0.1:8765
```

`-C/--root` 可以指定仓库里任意一个子目录。默认只绑 `127.0.0.1`，因为响应里带绝对路径。

## HTTP 接口

- `GET /api/state` → `{"root": ..., "worktrees": [...], "totals": {"count", "dirty", "behind", "prunable", "locked"}}`
- `GET /` → 单页 UI（`src/worktree_board/static/index.html`）
- 其余路径 → 404 JSON

每个 worktree 的字段：`path`、`branch`（detached 时为 `null`）、`head`、`main`、`locked`、`prunable`、`dirty`、`ahead`/`behind`（无 upstream 时为 `null`）、`subject`、`committed_at`。

## 设计取舍

- **只读 v1**：不做 POST 创建/删除 worktree，写操作交给 worktrunk，这里只回答问题
- **标准库**：工具的价值在于「一个 checkout 里什么都没装也能起」；`http.server` 够用
- **确定性**：`scan.py` 只调 git、只解析 porcelain，测试用临时仓库真实跑一遍
- **会话维度**：下一步把 worktree 和本机的 agent 会话对上（谁在跑、跑了多久），那是这张表存在的理由

## 待办

- [ ] 把 worktree 与会话（pid / 会话目录）对上，页面上标「活跃」
- [ ] 给落后远程的 worktree 一键 `git fetch` 提示（仍然只读，只提示不执行）

## 开发

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t . -v
```

CI 跑 Python 3.10 与 3.12。`scripts/lab.py` 是每周调研/记录的控制面（从 dsh-daily-lab 带过来的）。
