# -*- coding: utf-8 -*-
"""config.example.py — 用法复制为 config.py 并按需修改。所有路径参数化，禁止硬编码私有路径。"""
from pathlib import Path

# 记忆镜像根（替换为你的库目录）
MEM_ROOT = Path(r"./example_data/mem")

# staging 待处理投稿目录
STAGING_DIR = Path(r"./example_data/staging")

# 审计输出
AUDIT_LOG = Path(r"./example_data/audit/log.md")
MANIFEST = Path(r"./example_data/audit/manifest.json")

# 层目录映射（相对 MEM_ROOT）
TIER_DIRS = {"long": "01-长期记忆", "medium": "02-中期记忆"}
