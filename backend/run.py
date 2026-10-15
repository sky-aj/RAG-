"""开发入口：python run.py"""
import logging

from app import create_app
from app.config import settings

# 评测/出题一轮要调几十次模型，同步接口跑起来就是好几分钟，
# 没有日志的话完全看不出卡在哪一步。开发入口里把 INFO 打开。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

app = create_app()

if __name__ == "__main__":
    print(f"[corpus] backend on http://{settings.server.host}:{settings.server.port}")
    print(f"[corpus] embedding={settings.embedding.model_name} chat={settings.llm.chat_model} qdrant={settings.qdrant.mode}")
    app.run(host=settings.server.host, port=settings.server.port, debug=True, use_reloader=False)
