# Blocker 1 验证结论与处置方案（Issue 14 Round-2 Review）

## Review 的两个事实错误

### 错误 1：974baa9d 运行记录"缺失"

Review 声称 `待删除/long-running/` 中没有 `974baa9d` 的命令记录、状态、退出码或失败日志。
**事实**：记录完整存在于本机：

```
待删除/long-running/issue14_final_regression_sweep_20260814_103731_974baa9d/
  command.json   (argv=9模块, cwd=仓库根, git_commit=dc2a44c..., worktree_clean=true, run_id=974baa9d-00a8-438c-a7d1-9ede88f6796f)
  status.json    (state=failed, exit_code=1, acceptance_evidence_eligible=true)
  exit-code.txt  (1)
  stderr.log     (完整失败堆栈, 149 tests ran, FAILED failures=1)
  stdout.log     (git 输出)
  command.log    (任务名 issue14-final-regression-sweep)
```

`待删除/` 被 `.gitignore` 忽略，该记录未被提交到 Git 分支——这与核对文档的推测一致
（"很可能仍保存在执行 Agent 的本机目录中"）。

### 错误 2："完整回归 149 项"

Review 把 149 项称为"完整回归"。**事实**（与核对文档一致）：149 项是 **9 模块定向回归集**，
占 892 项完整 video-workflow 套件的约 16.7%。Issue 14 评论原文是
"unittest across the nine recommended modules"。

## 失败本身的真实性

974baa9d 的 stderr 证实失败**真实存在过**：

```
FAIL: test_workflow_policy_check_reports_both_kernel (tests.video_workflow.test_issue14_platform_cutover)
AssertionError: {'classification': 'workflow_policy_current', ...} != {'classification': 'acceptance_v2_rejected', 'platform_statuses': None, 'returncode': 30}
Ran 149 tests in 4943.345s
FAILED (failures=1)
```

`acceptance_v2_rejected`（rc=30）由 `global_gate.py` 的 `_reject` 抛出。失败点位于
`workflow-policy-check` 的 `require_current` 或 `_validate_policy_evidence`（镜像/策略校验）。

## 当前代码上的复现验证（2026-08-15）

| 运行 | 结果 |
|---|---|
| 单跑 `test_workflow_policy_check_reports_both_kernel` | **OK** |
| 整模块 `test_issue14_platform_cutover`（11 项） | **OK** |
| 2 模块组合 `platform_cutover` + `exit_evidence`（30 项） | **OK** |
| 9 模块完整 sweep 复现（与 974baa9d 相同命令向量, run_id=bf50902e） | `test_workflow_policy_check_reports_both_kernel` **通过**（该测试在 sweep 第 1 模块内运行于干净的 dc2a44c 上）；但 sweep 后半程被并行 fix agent 的 git checkout 污染，issue43 模块出现 47 个 schema 门失败（不可信，需干净重跑） |

**结论**：当前代码与当前环境上，该失败**无法复现**。974baa9d（8月14日）与复现（8月15日）
的关键环境差异待查（q/.agents 镜像 mtime 8月5日至今未变；无其它已知差异）。

## 处置方案

1. **补交 974baa9d 证据**：将 `待删除/long-running/issue14_final_regression_sweep_20260814_103731_974baa9d/`
   强制加入 Git 提交（该目录此前未被提交是"证据缺口"的唯一真实成分）。
2. **干净重跑 9 模块 sweep**：在 fix agent 完成、无并行写入的条件下，重跑相同命令向量，
   保存持久化记录并提交。若通过，则证明该失败为一次性环境状态（时过境迁），
   与 Blocker 2/3 的代码缺陷不同类；若仍失败，则按真实回归缺陷修复。
3. **明确命名**：将"9 模块定向回归"与"892 项完整 video-workflow 套件"分开记录，
   不再混称"完整回归"。
4. 重新发布 Slice 13 evidence（Blocker 2/3 修复后）时，正式资格命令与 9 模块 sweep
   一并重跑并提交记录。
