from __future__ import annotations

import asyncio
import inspect
import io
import logging
import os
import sys
from typing import Any, Callable, Optional

from ..request import Request
from .wsgi import _FinalRedirectException, _FinalResponseException

logger = logging.getLogger("asok.asgi")


class ASGIMixin:
    """Mixin class for Asok that handles the ASGI protocol, lifespan events,
    request translation, and response delivery.
    """

    async def _asgi_call(
        self, scope: dict[str, Any], receive: Callable, send: Callable
    ) -> None:
        """Main ASGI entry point."""
        # Handle lifespan events (startup / shutdown)
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    for hook in getattr(self, "_on_startup", []):
                        try:
                            if inspect.iscoroutinefunction(hook):
                                await hook()
                            else:
                                res = hook()
                                if inspect.iscoroutine(res):
                                    await res
                        except Exception as e:
                            logger.error("Error in ASGI startup hook: %s", e)
                    await send({"type": "lifespan.startup.complete"})

                elif message["type"] == "lifespan.shutdown":
                    for hook in getattr(self, "_on_shutdown", []):
                        try:
                            if inspect.iscoroutinefunction(hook):
                                await hook()
                            else:
                                res = hook()
                                if inspect.iscoroutine(res):
                                    await res
                        except Exception as e:
                            logger.error("Error in ASGI shutdown hook: %s", e)
                    await send({"type": "lifespan.shutdown.complete"})
                    break
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close"})
            return

        if scope["type"] != "http":
            return

        # 1. Read request body chunks asynchronously
        body_chunks = []
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body_chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                return

        body = b"".join(body_chunks)

        # 2. Build WSGI-compatible environ dictionary from ASGI scope
        headers = {}
        for k, v in scope.get("headers", []):
            headers[k.decode("latin1").lower()] = v.decode("latin1")

        environ = {
            "REQUEST_METHOD": scope["method"],
            "SCRIPT_NAME": scope.get("root_path", ""),
            "PATH_INFO": scope["path"],
            "QUERY_STRING": scope.get("query_string", b"").decode("latin1"),
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8000",
            "SERVER_PROTOCOL": "HTTP/" + scope.get("http_version", "1.1"),
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": scope.get("scheme", "http"),
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": sys.stderr,
            "wsgi.multithread": True,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
            "asok.root": getattr(self, "root_dir", os.getcwd()),
            "asok.app": self,
            "asok.secret_key": self.config.get("SECRET_KEY"),
            "asok.asgi": True,
        }

        for k, v in headers.items():
            name = k.upper().replace("-", "_")
            if name in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                environ[name] = v
            else:
                environ[f"HTTP_{name}"] = v

        client = scope.get("client")
        if client:
            environ["REMOTE_ADDR"] = client[0]
            environ["REMOTE_PORT"] = str(client[1])

        request = Request(environ)

        # 3. Setup Request Context & Dispatch
        from ..context import request_var

        token = request_var.set(request)
        try:
            import secrets

            self.nonce = secrets.token_urlsafe(16)
            request._nonce = self.nonce

            # Force session load
            _ = request.session

            is_head = request.method == "HEAD"
            if is_head:
                request.method = "GET"

            if getattr(request, "_body_rejected", False):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"Request body too large",
                        "more_body": False,
                    }
                )
                return

            status_str = "200 OK"
            headers_list = []

            def start_response(
                status: str,
                headers: list[tuple[str, str]],
                exc_info: Optional[Any] = None,
            ) -> None:
                nonlocal status_str, headers_list
                status_str = status
                headers_list = headers

            # Call existing WSGI route/service handlers using start_response mock
            res = self._handle_options_request(request, environ, start_response)
            if res is not None:
                await self._send_captured_response(status_str, headers_list, res, send)
                return

            if request.path == "/__health":
                body_res = b'{"status":"ok"}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body_res)).encode("latin1")),
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": body_res,
                        "more_body": False,
                    }
                )
                return

            res = self._handle_reload_request(request, start_response)
            if res is not None:
                await self._send_captured_response(status_str, headers_list, res, send)
                return

            res = self._handle_admin_request(request, environ, start_response)
            if res is not None:
                await self._send_captured_response(status_str, headers_list, res, send)
                return

            res = self._handle_docs_request(request, start_response)
            if res is not None:
                await self._send_captured_response(status_str, headers_list, res, send)
                return

            res = self._handle_static_request(request, environ, start_response)
            if res is not None:
                await self._send_captured_response(status_str, headers_list, res, send)
                return

            # Dispatch Page Controller / Template
            try:
                result = self._dispatch_controller(request, environ)
                if inspect.iscoroutine(result):
                    result = await result
            except _FinalResponseException as fre:
                status_str = Request._STATUS_MAP.get(
                    fre.status_code, f"{fre.status_code} Unknown"
                )
                body_bytes = (
                    fre.body.encode("utf-8") if isinstance(fre.body, str) else fre.body
                )
                await send(
                    {
                        "type": "http.response.start",
                        "status": fre.status_code,
                        "headers": [
                            (
                                b"content-type",
                                f"{fre.content_type}; charset=utf-8".encode("latin1"),
                            )
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": body_bytes,
                        "more_body": False,
                    }
                )
                return
            except _FinalRedirectException as frde:
                status_code = int(frde.status_str.split(" ", 1)[0])
                asgi_headers = [
                    (k.lower().encode("latin1"), v.encode("latin1"))
                    for k, v in frde.headers
                ]
                await send(
                    {
                        "type": "http.response.start",
                        "status": status_code,
                        "headers": asgi_headers,
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"",
                        "more_body": False,
                    }
                )
                return

            # Finalize Response
            final_res = self._finalize_response(
                request, result, environ, is_head, start_response
            )
            await self._send_captured_response(
                status_str, headers_list, final_res, send
            )

        finally:
            from ..orm import close_all_db_connections

            close_all_db_connections()
            request_var.reset(token)

    async def _send_captured_response(
        self,
        status: str,
        headers: list[tuple[str, str]],
        body_iterable: Any,
        send: Callable,
    ) -> None:
        status_code = int(status.split(" ", 1)[0])
        asgi_headers = [
            (k.lower().encode("latin1"), v.encode("latin1")) for k, v in headers
        ]

        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": asgi_headers,
            }
        )

        if body_iterable:
            if isinstance(body_iterable, (list, tuple)):
                for chunk in body_iterable:
                    await send(
                        {
                            "type": "http.response.body",
                            "body": chunk,
                            "more_body": False,
                        }
                    )
            else:
                # Generator / iterator
                try:
                    for chunk in body_iterable:
                        if chunk:
                            await send(
                                {
                                    "type": "http.response.body",
                                    "body": chunk,
                                    "more_body": True,
                                }
                            )
                finally:
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b"",
                            "more_body": False,
                        }
                    )
        else:
            await send(
                {
                    "type": "http.response.body",
                    "body": b"",
                    "more_body": False,
                }
            )

    def _get_async_middleware_chain(self, core_layer: Callable) -> Callable:
        """Compose the user middleware handlers into an async callable chain."""
        if not self.middleware_handlers:
            return core_layer

        try:
            main_loop = asyncio.get_running_loop()
        except RuntimeError:
            main_loop = None

        chain = core_layer
        for mw_handle in reversed(self.middleware_handlers):

            def make_wrapper(mw, nxt):
                if inspect.iscoroutinefunction(mw):

                    async def async_nxt(req):
                        res = nxt(req)
                        if inspect.iscoroutine(res):
                            return await res
                        return res

                    async def async_wrapper(req):
                        return await mw(req, async_nxt)

                    return async_wrapper
                else:
                    # Sync middleware: must run in thread pool if next handler is async
                    def sync_wrapper(req):
                        return mw(req, lambda r: async_to_sync(nxt(r), loop=main_loop))

                    async def async_wrapper(req):
                        return await asyncio.to_thread(sync_wrapper, req)

                    return async_wrapper

            chain = make_wrapper(mw_handle, chain)
        return chain


def async_to_sync(
    awaitable: Any, loop: Optional[asyncio.AbstractEventLoop] = None
) -> Any:
    """Run an awaitable synchronously, starting a loop on a separate thread if needed."""
    if not inspect.isawaitable(awaitable):
        return awaitable
    try:
        # Check if there is already a running loop in the current thread
        asyncio.get_running_loop()
        # If there is, run it in a separate thread to prevent "asyncio.run() cannot be called from a running event loop"
        import threading
        from concurrent.futures import Future

        result_future = Future()

        def run_in_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                val = loop.run_until_complete(awaitable)
                result_future.set_result(val)
            except Exception as e:
                result_future.set_exception(e)
            finally:
                loop.close()

        t = threading.Thread(target=run_in_loop)
        t.start()
        t.join()
        return result_future.result()
    except RuntimeError:
        # No loop is running in the current thread, run thread-safely on target loop or fallback
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(awaitable, loop)
            return future.result()
        return asyncio.run(awaitable)
