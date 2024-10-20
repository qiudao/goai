from collections import namedtuple
import os

from leptonai.photon import Photon

import fastchat.serve.gradio_web_server
import fastchat.serve.openai_api_server


class Server(Photon):
    requirement_dependency = [
        "git+https://github.com/lm-sys/FastChat.git@974537e",
    ]

    def _init_gradio_web_server(self):
        fastchat.serve.gradio_web_server.controller_url = os.environ.get(
            "CONTROLLER_ADDR", "http://0.0.0.0:21001"
        )
        fastchat.serve.gradio_web_server.enable_moderation = False
        self._models = fastchat.serve.gradio_web_server.get_model_list(
            fastchat.serve.gradio_web_server.controller_url, False, False, False
        )

        FakeArgs = namedtuple(
            "Args", ["model_list_mode", "add_chatgpt", "add_claude", "add_palm"]
        )
        fastchat.serve.gradio_web_server.args = FakeArgs(
            model_list_mode="reload",
            add_chatgpt=False,
            add_claude=False,
            add_palm=False,
        )

    def _init_openai_api_server(self):
        fastchat.serve.openai_api_server.app_settings.controller_address = (
            os.environ.get("CONTROLLER_ADDR", "http://0.0.0.0:21001")
        )
        fastchat.serve.openai_api_server.app_settings.api_keys = None

    def init(self):
        self._init_gradio_web_server()
        self._init_openai_api_server()

    @Photon.handler(path="api", mount=True)
    def openai_api_server_subapp(self):
        return fastchat.serve.openai_api_server.app

    @Photon.handler(path="chat", mount=True)
    def gradio_web_server_subapp(self):
        demo = fastchat.serve.gradio_web_server.build_demo(self._models)
        return demo.queue(concurrency_count=10, status_update_rate=10, api_open=False)
