# Dalamud Plugins CN

## 简介

由 [pearl-eye](https://github.com/pearl-eye) 维护的国服卫月汉化插件。

目前收录 [Allagan Tools CN](https://github.com/pearl-eye/Allagan-Tools-CN)。

## 安装方式

在国服卫月设置的自定义插件仓库中添加并启用：

```text
https://raw.githubusercontent.com/pearl-eye/Dalamud-Plugins-CN/main/repo.json
```

保存后打开插件安装器，搜索需要的插件并自行选择安装。添加仓库仅提供插件列表，不会自动安装列表中的全部插件。插件是否可用，以安装器显示的版本和当前卫月兼容状态为准。

如果已添加某个插件的独立仓库，可改用此总索引，避免重复添加同一来源。切换来源前，请禁用旧实例并备份相关配置，不要同时加载同一插件的多个实例。

## 反馈与贡献

仓库地址、插件列表或下载链接的问题，请提交到 [本仓库 Issues](https://github.com/pearl-eye/Dalamud-Plugins-CN/issues)。具体插件的翻译、界面和功能问题，请前往对应插件的源码仓库反馈，并提供插件版本、问题所在窗口和复现步骤。

[sources.json](sources.json) 记录各插件的发布索引来源。提出收录或修正时，请提供插件名称、源码仓库、发布索引地址及许可证信息。

维护时，在 `sources.json` 添加插件发布索引，运行 `python scripts/update_index.py`，再提交生成的 `repo.json`。总索引目前手动更新。
