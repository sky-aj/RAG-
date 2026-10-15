"""pytest \u516c\u5171\u914d\u7f6e\u3002

\u6700\u91cd\u8981\u7684\u4e00\u4ef6\u4e8b\uff1a\u5728\u4efb\u4f55\u6a21\u5757\u5bfc\u5165 app.config \u4e4b\u524d\uff0c\u628a\u5411\u91cf\u5e93\u8def\u5f84\u6307\u5230\u5de5\u4f5c\u533a\u5185\u7684\u4e34\u65f6\u76ee\u5f55\u3002
\u4e0d\u7136\u8dd1\u4e00\u6b21\u6d4b\u8bd5\u5c31\u4f1a\u5728 backend/data/qdrant \u91cc\u7559\u4e0b\u6b8b\u7559 collection\uff0c
\u6c61\u67d3\u771f\u5b9e\u5f00\u53d1\u73af\u5883\uff1b\u800c\u4e14\u6c99\u7bb1\u91cc\u5199\u4e0d\u4e86\u7cfb\u7edf\u4e34\u65f6\u76ee\u5f55\u3002
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

WORKSPACE_TMP = (Path(__file__).resolve().parent.parent / "data" / "tmp" / "qdrant-test").resolve()
shutil.rmtree(WORKSPACE_TMP, ignore_errors=True)

os.environ["QDRANT_PATH"] = str(WORKSPACE_TMP)
# 入库状态文件也必须跟着走：否则跑一次测试会把真实的
# data/index_state.json 改掉，前端就会凭空多出「待入库」的文档。
os.environ["INDEX_STATE_PATH"] = str(WORKSPACE_TMP.parent / "index_state.json")
os.environ["DOCUMENT_REGISTRY_PATH"] = str(WORKSPACE_TMP.parent / "documents.json")
os.environ["QDRANT_PATH"] = str(WORKSPACE_TMP)
os.environ["UPLOAD_DIR"] = str(WORKSPACE_TMP.parent / "uploads")
os.environ["EVAL_DATASET_PATH"] = str(WORKSPACE_TMP.parent / "eval" / "dataset.json")
os.environ["EVAL_REPORT_DIR"] = str(WORKSPACE_TMP.parent / "eval")


@pytest.fixture()
def scratch_dir():
    """工作区内的一次性目录。

    不用 pytest 自带的 tmp_path：它会给目录加一套受限 ACL，
    当前用户连列目录都会被拒绝，放到工作区里反而更干净。
    """
    base = WORKSPACE_TMP.parent / "scratch"
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture()
def isolated_state(scratch_dir, monkeypatch):
    """把入库状态文件与文档注册表指到临时目录。

    注意：`settings` 是导入时就构造好的单例，它里面的
    Path 字段已经定死，之后再改环境变量不会生效。
    所以这里直接把模块里引用的 settings 对象整个换掉。
    """
    from dataclasses import replace

    from app import config as config_module
    from app.api import documents as documents_api
    from app.retrieval import indexer

    patched = replace(
        config_module.settings,
        index=replace(
            config_module.settings.index,
            state_path=scratch_dir / "index_state.json",
            registry_path=scratch_dir / "documents.json",
        ),
    )
    for module in (indexer, documents_api):
        monkeypatch.setattr(module, "settings", patched)
    return patched

@pytest.fixture()
def client():
    """Flask 测试客户端。步骤 2 起多个测试文件都要用，放这里共享。"""
    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client
