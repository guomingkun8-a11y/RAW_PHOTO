用于让Bot扮演指定角色的聊天插件，触发方法如下：

- `$角色/$role help/帮助` - 打印目前支持的角色列表。
- `$角色/$role <角色名>` - 让AI扮演该角色，角色名支持模糊匹配。
- `$停止扮演` - 停止角色扮演。

## 目录结构

```
plugins/role/
├── README.md
├── __init__.py
├── role.py                  # 插件主逻辑
├── roles.json               # 内置角色库（所有默认角色）
├── schema/
│   └── role_template.json   # 角色模板，新增角色时复制此文件
└── roles/                   # 可选：放自定义角色文件，自动加载
    └── 我的角色.json         # 同title会覆盖内置角色，不同则追加
```

## 添加自定义角色

1. 新建roles目录
2. 复制 下面模板 到 `roles/` 目录并重命名（如 `我的角色.json`）。
3. 编辑文件内容，填写角色信息。
4. 重启Bot即可生效，插件会自动扫描 `roles/` 目录下的所有 `.json` 文件。

> **覆盖规则**：如果自定义角色的 `title` 与内置角色同名，则覆盖内置角色；否则作为新角色追加。无需修改任何映射文件。

(大部分prompt来自https://github.com/rockbenben/ChatGPT-Shortcut/blob/main/src/data/users.tsx)

以下为角色文件内容例子:
```json
{
  "title": "写作助理",
  "description": "As a writing improvement assistant...",
  "descn": "作为一名中文写作改进助理...",
  "wrapper": "内容是:\n\"%s\"",
  "remark": "最常使用的角色，用于优化文本的语法、清晰度和简洁度，提高可读性。",
  "tags": ["favorite", "write"]
}
```

- `title`: 角色名。
- `description`: 使用`$role`触发时，使用英语prompt。
- `descn`: 使用`$角色`触发时，使用中文prompt。
- `wrapper`: 用于包装用户消息，可起到强调作用，避免回复离题。
- `remark`: 简短描述该角色，在打印帮助文档时显示。
- `tags`: 角色分类标签，可用`$角色类型 <标签>`查看。


```json
{
  "title": "",
  "description": "",
  "descn": "",
  "wrapper": "",
  "remark": "",
  "tags": []
}
```