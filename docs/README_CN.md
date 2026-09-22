# Grounded — 快速指南（中文）

> 简要译文，以[英文 README](https://github.com/gonisulaimann/Grounded#readme) 为准。

**Grounded** 是引用完整性防火墙：它找出与仓库相矛盾的注释、文档示例、导入和配置字符串——已不存在的函数、缺失的文件、损坏的导入、失效的示例。确定性、离线、零依赖。支持 Python、JavaScript/TypeScript、Go 和 C。

## 安装

```console
pip install grounded-lint
```

```console
brew install gonisulaimann/tap/grounded
```

## 使用

```console
grounded scan .                    # 扫描仓库（发现错误时退出码为 1）
grounded scan . --changed          # 仅检查修改的行（适合 CI）
grounded fix . --dry-run           # 预览修复内容
grounded impact my_function .      # 查看与某个符号相关的一切：定义、导入方
```

## 规则

| ID | 严重级别 | 检查内容 |
|---|---|---|
| `stale-symbol-ref` | lie | 注释中提到的函数在仓库中不存在 |
| `stale-import` | lie | 可解析的导入指向缺失的模块或未定义的名称 |
| `stale-file-ref` | lie | 注释指向仓库中不存在的路径 |
| `number-drift` | drift | 注释中的数字与相邻代码矛盾 |
| `fragile-anchor` | smell | 脆弱的行号锚点、无工单的 workaround 标记 |
| `stale-entrypoint` | lie | `pyproject` scripts 或 `package.json` bin/main 指向仓库中不存在的目标 |
| `stale-mock-ref` | lie | `@patch` 字符串提到的符号在对应模块中不存在 |
| `unclosed-fence` | lie | 渲染器无法闭合的 Markdown 代码围栏——其后内容按代码渲染 |

13 条规则中 8 条默认启用；另有 5 条需手动开启（`--enable <id>`）。

## 链接

- [完整文档](https://grounded.readthedocs.io/en/latest/)（英文）
- [报告问题](https://github.com/gonisulaimann/Grounded/issues)
- 许可证：MIT
