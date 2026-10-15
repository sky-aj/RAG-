from flask import Blueprint, jsonify, request

from .. import knowledge

bp = Blueprint("knowledge_bases", __name__)


@bp.get("/knowledge-bases")
def list_labels():
    return jsonify({"items": knowledge.list_labels()})


@bp.post("/knowledge-bases")
def create_label():
    return jsonify({"item": knowledge.create_label((request.get_json(silent=True) or {}).get("name"))}), 201


@bp.patch("/knowledge-bases/<kb_id>")
def rename_label(kb_id):
    return jsonify({"item": knowledge.rename_label(kb_id, (request.get_json(silent=True) or {}).get("name"))})


@bp.delete("/knowledge-bases/<kb_id>")
def delete_label(kb_id):
    knowledge.delete_label(kb_id)
    return jsonify({"deleted": kb_id})
