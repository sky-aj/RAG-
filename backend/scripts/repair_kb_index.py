"""为指定分类补做增量入库并验证检索；不重建已有向量、不调用生成模型。"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import knowledge, rag
from app.api import documents
from app.config import settings
from app.retrieval import indexer, vector_store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kb_id")
    parser.add_argument("--query", required=True)
    args = parser.parse_args()
    label = knowledge.require_label(args.kb_id, destination=True)
    ids = set(knowledge.resolve_scope({"kb_id": args.kb_id}))
    records = [item for item in documents.load_registry() if item["doc_id"] in ids]
    if not records:
        raise SystemExit("指定分类没有文件")
    backup = settings.backend_dir / "data" / "tmp" / ("kb-index-repair-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(parents=True)
    for path in (documents.registry_path(), indexer.state_path(), knowledge.registry_path()):
        if path.exists():
            shutil.copy2(path, backup / path.name)
    before = {item["doc_id"]: item["chunks"] for item in vector_store.indexed_documents()}
    report = indexer.index_documents(records, force=False)
    after = {item["doc_id"]: item["chunks"] for item in vector_store.indexed_documents()}
    assert all(after.get(doc_id) == chunks for doc_id, chunks in before.items()), "已有文档向量数量发生变化"
    assert set(after) - set(before) <= ids, "写入了范围外文档"
    result = rag.retrieve(args.query, doc_ids=knowledge.resolve_retrieval_scope({"kb_id": args.kb_id}),
                          use_rerank=False, use_rewrite=False)
    summary = {"kb": label["name"], "documents": len(records), "written": report["written"],
               "skipped": len(report["skipped"]), "failed": report.get("failed", []),
               "indexed": {item["name"]: after.get(item["doc_id"], 0) for item in records},
               "retrieved": sorted({item["doc_name"] for item in result["citations"]}),
               "existing_vectors_preserved": True, "backup": str(backup)}
    (backup / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not report["ok"] or not result["citations"]:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    finally:
        vector_store.close_client()
