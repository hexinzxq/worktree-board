# 取舍记录

## 2026-11-09 没做「worktree ↔ 会话」映射

想做的第一件事是把每个 worktree 对应到在它上面干活的 agent 会话，看板上直接显示「这块是哪个会话在用」。

查了本机的 `~/.dsh/storages/session_projcache/sessions/`：778 个会话文件，能读到的 `record.identity.cwd` 只有 3 个不同值——

- 643 个 `/Users/xin/.dsh/im`
- 124 个 `/Users/xin/.dsh/bili-duty`
- 11 个 `/Users/xin/Documents/deepseek-harness/default-workspace`

一个都不落在某个 worktree 路径上。也就是说在这个环境里，「会话开在 worktree 里」这个前提不成立：会话开在固定的几个工作目录，git 操作是在里面再 `cd` 过去的。

结论：先不做。按 cwd 前缀匹配能做出来的唯一效果是「什么都匹配不上」，那比不做更糟——一块永远空着的列会让人以为工具坏了。真要做，得先让会话自己声明「我在给哪个 worktree 干活」（比如一个 `.worktree-board` 标记文件或环境变量），那是另一个设计，等有人真的多 worktree 并行干活时再说。

顺带记一笔：读 778 个缓存文件大约 188ms，做实时轮询也不合适，真要做得先有增量读取的办法。
