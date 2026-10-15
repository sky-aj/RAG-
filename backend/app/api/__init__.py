"""所有蓝图在这里注册，新增步骤时加一行即可。"""
from . import chat, chunks, documents, eval, health, index, knowledge_bases

BLUEPRINTS = (health.bp, documents.bp, chunks.bp, index.bp, chat.bp, eval.bp, knowledge_bases.bp)


def register_blueprints(app) -> None:
    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint, url_prefix="/api")
