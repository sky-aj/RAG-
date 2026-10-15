"""Flask 应用工厂。

分层约定：
  api/       路由，只负责收参和返回
  ingest/    解析与分块（步骤 2、3）
  retrieval/ 向量化、向量库、混合检索、上下文拼装（步骤 4、5）
  llm/       智谱客户端（步骤 5）
  eval/      评测与追踪（步骤 6）
"""
from __future__ import annotations

from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

from .config import settings


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
    app.config["JSON_AS_ASCII"] = False

    CORS(app, resources={r"/api/*": {"origins": settings.server.cors_origins or "*"}})

    from .api import register_blueprints

    register_blueprints(app)

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify({"error": "接口不存在"}), 404

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify({"error": "文件超过 50 MB 上限"}), 413

    @app.errorhandler(Exception)
    def unhandled(error):
        if isinstance(error, HTTPException):
            return jsonify({"error": error.description}), error.code
        app.logger.exception("未处理异常")
        return jsonify({"error": str(error), "type": type(error).__name__}), 500

    return app
