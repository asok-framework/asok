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

    async def _run_lifespan_hooks(self, attr_name: str) -> None:
        for hook in getattr(self, attr_name, []):
            try:
                if inspect.iscoroutinefunction(hook):
                    await hook()
                else:
                    res = hook()
                    if inspect.iscoroutine(res):
                        await res
            except Exception as e:
                logger.error("Error in ASGI %s hook: %s", attr_name, e)

    async def _handle_asgi_lifespan(self, receive: Callable, send: Callable) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await self._run_lifespan_hooks("_on_startup")
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await self._run_lifespan_hooks("_on_shutdown")
                await send({"type": "lifespan.shutdown.complete"})
                break

    async def _read_asgi_body(self, receive: Callable) -> Optional[bytes]:
        body_chunks = []
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body_chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                return None
        return b"".join(body_chunks)

    def _build_environ_from_asgi(self, scope: dict[str, Any], body: bytes) -> dict[str, Any]:
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

        return environ

    async def _send_error_response(self, status: int, body: bytes, send: Callable, content_type: bytes = b"text/plain") -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", content_type),
                    (b"content-length", str(len(body)).encode("latin1")),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
                "more_body": False,
            }
        )

    async def _send_final_response_exception(self, fre: _FinalResponseException, send: Callable) -> None:
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

    async def _send_final_redirect_exception(self, frde: _FinalRedirectException, send: Callable) -> None:
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

    async def _run_dispatch_handlers_extended(
        self, request: Request, environ: dict[str, Any], start_response: Callable
    ) -> Optional[Any]:
        res = self._handle_docs_request(request, start_response)
        if res is not None:
            return res

        res = self._handle_graphql_request(request, start_response)
        if res is not None:
            return res

        res = self._handle_static_request(request, environ, start_response)
        if res is not None:
            return res

        res = self._handle_ssg_isr_request(request, environ, start_response)
        if res is not None:
            return res

        return None

    async def _run_dispatch_handlers(
        self, request: Request, environ: dict[str, Any], start_response: Callable
    ) -> Optional[Any]:
        res = self._handle_options_request(request, environ, start_response)
        if res is not None:
            return res

        res = self._handle_reload_request(request, start_response)
        if res is not None:
            return res

        res = self._handle_admin_request(request, environ, start_response)
        if res is not None:
            return res

        return await self._run_dispatch_handlers_extended(request, environ, start_response)

    def _prepare_dispatch_request(self, request: Request) -> tuple[bool, bool]:
        import secrets
        request._nonce = secrets.token_urlsafe(16)

        is_head = request.method == "HEAD"
        if is_head:
            request.method = "GET"
        return is_head, getattr(request, "_body_rejected", False)

    async def _execute_controller(self, request: Request, environ: dict[str, Any]) -> Any:
        result = self._dispatch_controller(request, environ)
        if inspect.iscoroutine(result):
            return await result
        return result

    def _cleanup_dispatch_request(self, request: Request, token: Any) -> None:
        from ..context import request_var
        from ..orm import close_all_db_connections
        from .signals import request_finished

        try:
            request_finished.send(self, request=request)
        except Exception:
            pass
        close_all_db_connections()
        request_var.reset(token)

    async def _execute_and_send_response(
        self,
        request: Request,
        environ: dict[str, Any],
        is_head: bool,
        status_headers: list,
        start_response: Callable,
        send: Callable
    ) -> None:
        try:
            result = await self._execute_controller(request, environ)
            final_res = self._finalize_response(
                request, result, environ, is_head, start_response
            )
            await self._send_captured_response(
                status_headers[0], status_headers[1], final_res, send
            )
        except _FinalResponseException as fre:
            await self._send_final_response_exception(fre, send)
        except _FinalRedirectException as frde:
            await self._send_final_redirect_exception(frde, send)

    async def _dispatch_asgi_http(
        self, request: Request, environ: dict[str, Any], send: Callable
    ) -> None:
        from ..context import request_var
        token = request_var.set(request)
        try:
            from .signals import request_started
            request_started.send(self, request=request)

            is_head, body_rejected = self._prepare_dispatch_request(request)
            if body_rejected:
                await self._send_error_response(413, b"Request body too large", send)
                return

            status_headers = ["200 OK", []]

            def start_response(
                status: str,
                headers: list[tuple[str, str]],
                exc_info: Optional[Any] = None,
            ) -> None:
                status_headers[0] = status
                status_headers[1] = headers

            if request.path == "/__health":
                await self._send_error_response(200, b'{"status":"ok"}', send, content_type=b"application/json")
                return

            res = await self._run_dispatch_handlers(request, environ, start_response)
            if res is not None:
                await self._send_captured_response(status_headers[0], status_headers[1], res, send)
                return

            _ = request.session
            await self._execute_and_send_response(request, environ, is_head, status_headers, start_response, send)

        finally:
            self._cleanup_dispatch_request(request, token)

    async def _asgi_call(
        self, scope: dict[str, Any], receive: Callable, send: Callable
    ) -> None:
        """Main ASGI entry point."""
        if scope["type"] == "lifespan":
            await self._handle_asgi_lifespan(receive, send)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close"})
            return

        if scope["type"] != "http":
            return

        body = await self._read_asgi_body(receive)
        if body is None:
            return

        environ = self._build_environ_from_asgi(scope, body)
        request = Request(environ)
        await self._dispatch_asgi_http(request, environ, send)

    async def _send_iterable_chunks(self, body_iterable: Any, send: Callable) -> None:
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
            await self._send_iterable_chunks(body_iterable, send)
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
        for mw_handle in self._middleware_handlers_reversed:

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
