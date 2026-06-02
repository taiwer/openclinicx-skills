# Changelog

## endnote-management-1.2.0 (2025-07-10)

✨ **新增脚本目录**

- 新增 `scripts/` 文件夹，包含 3 个可直接调用的 Python 脚本
- `build_endnote.py` — 一键将方括号引用转为 EndNote 格式（Build Mode）
- `validate_endnote.py` — 转换后独立校验 EndNote 引用合法性
- `endnote_utils.py` — 共享工具函数，提取字段值与引文文本
- Agent 不再需要每次从头编写转换代码，直接调用脚本即可
- 更新 SKILL.md 重构 Agent 使用说明

## endnote-management-1.1.0 (2025-07-10)

🎉 **首次发布**

- 完成 endnote-management 技能的基础开发和测试
- 功能完整，可投入正常使用
- 包含 `SKILL.md` 主文件及 Endnote OOXML 金规参考文档
