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

### #4 刚 init 的仓库，表里最后提交那格是空的

- **改了什么**：`scan()` 认出 unborn HEAD（porcelain 里 `HEAD` 全是 0），`head` 置空串、`subject` 写成 `(no commits yet)`；页面在没有 head 时显示「(还没有提交)」而不是一个空单元格。新增 2 个用例（unborn 仓库的字段、表格里不出现 `000000000000`、也不留空）。
- **为什么**：`git init` 完立刻打开看板，是这个工具最先被用到的场景之一（今天新起的仓库就是这样）。原来的输出是一行：分支 main、最后提交空白、head 是一串 0 —— 看着像工具坏了，其实只是「还没有提交」。空白比错误更糟，因为它不解释自己。
- **验证**：19 个测试全过；真仓库复跑（临时 `git init` + report），`subject` 与表格都对上了。推送仍然卡在网络，见当天汇报。

### #5 「落后 0」到底什么时候算的

- **改了什么**：`scan()` 新增 `fetched_at`——读 `git rev-parse --git-common-dir` 下 `FETCH_HEAD` 的 mtime，转成 ISO 时间；`_table()` 在 `behind > 0` 时补一行 `note: behind is measured against the last fetch (…newest…) — run git fetch for a fresher number`；README 字段表补上这个字段。新增 2 个用例（推一次再 fetch 后 behind 是 1 且 `fetched_at` 非空、表格出现那句 note）。
- **为什么**：`@{upstream}` 比的是本地引用。三天没 fetch 的克隆上「落后 0」是句真话，但说的是三天前的快照——看板给数字不给新鲜度，等于让人拿旧地图找路。这一条和第 2 条是同一个毛病：数字本身对，读者理解错。
- **验证**：21 个测试全过（upstream 用裸仓库 + 另一个克隆推一次来造「落后 1」的真实局面）。推送仍受网络影响，见当天收口汇报。

### #6 中文提交主题把表格推歪了

- **改了什么**：`cli.py` 的表格改成按「终端格子」排版——新增 `_width()`（`unicodedata.east_asian_width` 里 `W`/`F` 记 2 格）与 `_pad()`，`_cell()` 截断也改按格子算（中文就不至于截出半个字）。新增 2 个用例：中文目录 + 中文提交的仓库，所有行的 PATH 必须从同一列开始；20 格的预算里截「修复看板里中文提交主题的对齐问题」必须以 `…` 结尾且宽度 ≤ 20。
- **为什么**：这台机器上提交主题和目录名本来就常是中文。`len()` 把「修」当 1 格，`ljust` 就少补一格，PATH 一列整排右移——今天用 `中文 仓库` + 中文提交主题真跑了一遍，LAST COMMIT 和 PATH 两列肉眼可见地歪。表格是这个工具唯一的输出，歪了就等于没对齐。
- **验证**：23 个测试全过；同一场景复跑，两行 PATH 起点对齐。

### #7 网页版没有那句「落后是相对哪次 fetch 算的」

- **改了什么**：`static/index.html` 在统计条下面加一行说明——只要有落后，就写出「对着最后一次 fetch 的引用算的（时间）；要更准就 git fetch 一次」，没有拉取记录时写「还没 fetch 过」。新增 1 个用例（页面里必须同时出现 `fetched_at`、「最后一次 fetch」和 `id="fresh"`，防止以后改版把它悄悄删掉）。
- **为什么**：第 5 条只把新鲜度补给了终端表格，网页那边还在裸报「落后 3」。同一个数字在两个前端里说不一样的话，用户会以为表格在甩锅。
- **验证**：24 个测试全过；顺手拿自己的 sloplint 扫了页面，`clean: 0 tell(s) wairable, 0 failures`。

### #8 「不在仓库里」和「这是个裸仓库」不是一句话

- **改了什么**：`repo_root()` 原来把 `git rev-parse --show-toplevel` 的 stderr 原样抛出来（`git rev-parse … failed in /private/tmp: fatal: not a git repository…`）。现在分三种情形说人话：工作树（正常返回）、裸仓库（`is a bare repository — it has no worktrees to show`）、`-C` 指到了 `.git` 目录（`is a git directory, not a working tree — pass the repository root`）、其余 `is not inside a git repository`。新增 3 个用例各覆盖一句。
- **为什么**：这三种情况用户要做的下一步完全不同——换目录、去裸仓库的兄弟目录、或者把 `.git` 去掉。原样抛 git 的英文 stderr，等于让人自己猜。
- **验证**：29 个测试全过；`ls -C /tmp`、`ls -C bare.git`、`ls -C repo/.git` 三条真路径手跑过，文案对得上。

### #9 上游分支被删了，别报「落后 0」

- **改了什么**：`_ahead_behind()` 多返回一个 `upstream_gone`。`@{upstream}` 不可用时不再一律当成「没有上游」，而是查 `branch.<名>.remote` / `.merge` 配置——配置还在、远端追踪引用没了（`fetch --prune` 之后就是被删了），标记 `upstream_gone`；表格的 BEHIND 列写 `gone` 并在表尾提示怎么重新指上游，页面加「上游已删」徽章并把那格写 `gone`。新增 2 个用例（裸仓库造「推上去→远端删分支→prune」，字段和表格各验一次）。
- **为什么**：PR 合完、远端分支删掉，本地分支的 upstream 配置还在。原来这时 ahead/behind 都是 `null`，表格显示 `-`，读者会理解成「不在跟踪任何上游」，实际是「跟踪的那个已经没了」——下一步该做的是重新指上游或者合并删除，而不是继续等它出现。
- **验证**：29 个测试全过；真仓库复跑，`feat` 行显示 `gone` + 那句 note。

### #10 长分支名截掉了唯一能区分行的那截

- **改了什么**：`_cell()` 加了 `keep_tail`——分支名超宽时保留头尾两端（`claude/session-2…-flaky-test`），提交主题仍按左侧截断。路径列把家目录前缀缩成 `~`（只影响显示，JSON 里还是绝对路径）。新增 3 个用例（两条只差尾部长分支必须显示不同、短分支不动、`_short_path` 有 home/无 home 两种）。
- **为什么**：agent 开出来的分支名都长成 `claude/session-2026-11-10-<主题>`，前 28 列完全一样。原来从右边截，两行表格长得一模一样，唯一的区别在省略号后面——那等于没说。原样跑了两条 `review-fix-flaky-test` / `review-fix-docs`，肉眼确认修好了。
- **验证**：32 个测试全过；同一场景复跑，两行尾部能分开。

### #11 汇总行说「0 落后」，可那一行写着 gone

- **改了什么**：`report()["totals"]` 加 `upstream_gone`；表格汇总行在有上游已删时补 `, 1 upstream gone`；网页统计条多一个「N 个上游已删」chip。测试补两处断言（totals 与行一致、汇总文案出现）。
- **为什么**：第 9 条让 BEHIND 格写 `gone`，但汇总行还是 `0 behind upstream`——同一张表里两个数字互相否认。汇总行是很多人唯一会读的一行，它得和明细一致。
- **验证**：32 个测试全过；真仓库复跑，汇总行 `1 worktree(s), 0 dirty, 0 behind upstream, 1 upstream gone`。

### #12 裸仓库也能有自己的 worktree，别一口回绝

- **改了什么**：`repo_root()` 遇到裸仓库不再抛错——`git worktree list` 在裸仓库里本来就能用，返回 `--absolute-git-dir` 作为根；`_parse_list` 认识 `bare` 那一行；`Worktree` 加 `bare` 字段，表格第一列写 `(bare repository)`、页面对应徽章「裸仓库」，并且裸条目不再被当成主工作树。原用例改成「裸仓库是仓库不是错误」，另加一个「裸仓库 + `worktree add` 出来的检出，两行都不错」。
- **为什么**：上一轮（`d518379`）我自己写的那句「裸仓库没有 worktree 可看」是错的。`git worktree add` 完全可以在裸仓库里执行，托管用裸仓库 + 检出放别处是很正常的布局。错误消息写得斩钉截铁，比不说还糟。
- **验证**：33 个测试全过；真造了一个裸仓库 + 一个检出，表格两行：`(bare repository)`（不标主）和 `main`。

### #13 表格只说「最后一次提交」，不说多久以前

- **改了什么**：`wtree ls` 加一列 `AGE`（`2d` / `3h` / `5m` / `just now`），从 `committed_at` 算，`--json` 不变；新加 2 个用例（单位换算与不可解析时间的 `?`，表格里确实出现 AGE 列）。
- **为什么**：看板是拿来判断「这条 worktree 是不是死了」的。北京时间 2026-11-10 复跑 skillforge 那行，LAST COMMIT 只写 `read tokens from a pag…`，看不出这是两天前还是两分钟前的提交；网页那边本来就有完整时间戳，CLI 反而缺。一个日期挤成 `2d` 才是一眼能扫的量。
- **验证**：35 个测试全过；对 `/Users/xin/Documents/deepseek-harness/default-workspace/skillforge` 实跑，`AGE` 列显示 `2d`，与 git log 时间吻合。

### #14 分离头指针那一行把 40 位 sha 全印出来

- **改了什么**：detached worktree 的分支列从 `(detached <40 位 sha>)` 改成 `(detached 2913c94)`（7 位），网页同一处一起改；新加 1 个用例（表格里出现短 sha、整串 sha 不在输出里）。
- **为什么**：真造了个 `--detach` 的 worktree 才看见——28 列的分支格被撑到 52 个字符，网页那一格直接把整行顶宽。要认那条提交，7 位足够 `git show`，40 位只配出现在 `--json` 里。
- **验证**：36 个测试全过；同一个分离头指针场景复跑，分支列回到 `(detached 2913c94)`，表格其余列不再被推歪。

### #15 缺了页面文件时，服务器直接断连

- **改了什么**：`GET /` 找不到 `static/index.html` 时返回 500 + `{"error": "missing … — the installed package has no page; reinstall worktree-board"}`，不再让 `FileNotFoundError` 把连接掐掉；新加 1 个用例（把 `INDEX` 指到不存在的路径，断言 500 且文案里出现 `reinstall`）。
- **为什么**：本机没装 setuptools（`ModuleNotFoundError: No module named 'setuptools'`），`pip install --no-build-isolation .` 直接失败——也就是说这个包的打包路径从 11-07 之后就没被本地验证过一次，全靠 CI。而 CI 现在推不上去。`pyproject.toml` 里 `[tool.setuptools.package-data] worktree_board = ["static/*.html"]` 写了，但真丢了页面时，浏览器只会看到空白，一句解释都没有。
- **验证**：37 个测试全过；同一场景手测，返回 500 JSON，不再是断连。

### #16 让「页面没进包」这件事在测试里就挂掉

- **改了什么**：新增 `tests/test_packaging.py` 四个检查——`package-data` 里有 `static/*.html`、被承诺的页面文件真的存在、`wtree` 入口指向 `worktree_board.cli:main`、仓库里没有跟踪任何 `.pyc`。Python 3.10 没有 `tomllib`，那两条自动 skip。
- **为什么**：昨天那条「缺页面就断连」是治标。真正的问题是本机没有 setuptools（`pip install --no-build-isolation .` 直接报 `BackendUnavailable`），安装路径只能靠 CI 验证，而 CI 推不上去。把「页面必须进包」写成测试，至少下次改 `pyproject.toml` 时有人接住。
- **验证**：41 个测试全过。

### #17 加 `wtree find`：先找到 worktree 在哪

- **改了什么**：新子命令 `wtree find -C <目录>`，从目录往下走（最多 6 层）找带 `.git` 的检出，只列出 worktree 数 ≥ `--min`（默认 2）的仓库；`--json` 给 `{root, repos[]}`；找到仓库就不再往里走。`scan.find_repos()` 负责这件事。新增 2 个用例（只报 ≥2 的仓库、排序与计数对；走进仓库内部不再重复计数）。
- **为什么**：跑 `find -C ~/Documents -type d -name worktrees` 得到**零结果**——这台机器上一个仓库都没开过 linked worktree，看板的表至今只在测试夹具和自建 demo 上跑过。既然「worktree 在哪」是第一步，就先给个步骤。顺带在真目录上验证：`no repository with 2+ worktree(s) under ~/Documents/deepseek-harness/default-workspace`，是实话。
- **坑**：第一版把 linked worktree 当成独立仓库（它的 `.git` 是个文件）→ 同一个仓库报两遍。改成 `.git` 是文件就跳过（归属仓库会报它），`.git` 是目录才算检出。
- **验证**：43 个测试全过；真目录实跑输出上面那句。

### #18 「没找到」和「没看到那么深」得分开说

- **改了什么**：`find` 加 `--depth`（默认 6）；新增 `scan.survey()` 返回 `{root, repos, depth_cut}`，`find_repos()` 变成它的薄壳。深度用完还有目录没走进去时，stderr 打 `note: stopped at 1 level(s) in 6 directories — pass --depth to go deeper`；`--json` 里也有 `depth_cut`。新增 1 个用例（浅走要计数、走够就是 0）。
- **为什么**：晚上在真目录上拿 `--depth 1` 试，屏幕上只写「no repository…」——听着像「这里什么都没有」，其实是「我一层也没往下看」。sloplint 那边为同一件事改过两次（`not scanned`、`--exclude`），这里不该再犯。
- **验证**：44 个测试全过；`find -C … --depth 1` 实跑：结论行照旧，stderr 多那句 note。
