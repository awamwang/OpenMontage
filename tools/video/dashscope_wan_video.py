"""DashScope (阿里云百炼) Wan 3.0 视频生成。

走 DashScope 原生异步视频合成接口（非 OpenAI 兼容模式）：
POST /api/v1/services/aigc/video-generation/video-synthesis
Header: X-DashScope-Async: enable
→ poll GET /api/v1/tasks/{task_id} → 下载 output.video_url

本地首帧通过 data:image/...;base64,... 传入 media[].url，无需公网托管。
"""

from __future__ import annotations

import base64
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


class DashscopeWanVideo(BaseTool):
    name = "dashscope_wan_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "dashscope"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.ASYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Set DASHSCOPE_API_KEY to your Alibaba Cloud DashScope API key.\n"
        "  Get one at https://dashscope.aliyun.com/"
    )
    fallback = "wan_fal_video"
    fallback_tools = ["wan_fal_video", "wan_atlas_video", "wan_replicate_video"]
    agent_skills = ["dashscope", "ai-video-gen"]

    capabilities = ["text_to_video", "image_to_video"]
    supports = {
        "text_to_video": True,
        "image_to_video": True,
        "first_last_frame": True,
        "native_audio": True,
        "seed": True,
        "negative_prompt": False,
        "local_first_frame": True,
    }
    best_for = [
        "阿里云百炼 Wan3.0 / Wan3.0-Prime 文生视频与首帧图生视频",
        "已有定稿首帧、需要锁定开场构图的竖屏短剧",
        "中文提示词理解较好的云端视频生成",
    ]
    not_good_for = [
        "离线本地生成",
        "需要精确 16fps / 非整数秒时长的 ComfyUI 工作流复刻",
    ]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {
                "type": "string",
                "minLength": 1,
                "maxLength": 5000,
                "description": "视频描述。图生视频时描述首帧之后的运动与表演。",
            },
            "operation": {
                "type": "string",
                "enum": ["text_to_video", "image_to_video"],
                "default": "text_to_video",
            },
            "model": {
                "type": "string",
                "enum": ["wan3.0-video-prime", "wan3.0-video"],
                "default": "wan3.0-video-prime",
            },
            "image_path": {
                "type": "string",
                "description": "本地首帧图片路径（image_to_video）。会编码为 data URI。",
            },
            "image_url": {
                "type": "string",
                "description": "首帧公网 URL 或 data URI（与 image_path 二选一）。",
            },
            "last_frame_path": {
                "type": "string",
                "description": "可选末帧本地路径；与首帧一起构成首末帧模式。",
            },
            "last_frame_url": {
                "type": "string",
                "description": "可选末帧公网 URL 或 data URI。",
            },
            "resolution": {
                "type": "string",
                "enum": ["480P", "720P", "1080P"],
                "default": "480P",
            },
            "ratio": {
                "type": "string",
                "enum": [
                    "adaptive",
                    "21:9",
                    "16:9",
                    "4:3",
                    "1:1",
                    "3:4",
                    "9:16",
                ],
                "default": "adaptive",
                "description": "图生视频建议 adaptive，按首帧宽高比输出。",
            },
            "duration": {
                "type": "integer",
                "minimum": 2,
                "maximum": 30,
                "default": 5,
                "description": "输出时长（秒）。仅支持整数秒。",
            },
            "audio": {
                "type": "boolean",
                "default": False,
                "description": "是否生成原生对白/BGM/音效。默认关闭，便于沿用项目 TTS。",
            },
            "prompt_extend": {
                "type": "boolean",
                "default": False,
                "description": "是否让服务端改写提示词。定稿镜头建议关闭。",
            },
            "watermark": {"type": "boolean", "default": False},
            "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
            "poll_interval_seconds": {
                "type": "number",
                "minimum": 1,
                "maximum": 60,
                "default": 5.0,
            },
            "timeout_seconds": {
                "type": "integer",
                "minimum": 30,
                "maximum": 1800,
                "default": 900,
            },
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=200, network_required=True
    )
    retry_policy = RetryPolicy(
        max_retries=2, retryable_errors=["rate_limit", "timeout"]
    )
    idempotency_key_fields = [
        "prompt",
        "operation",
        "model",
        "image_path",
        "image_url",
        "last_frame_path",
        "last_frame_url",
        "resolution",
        "ratio",
        "duration",
        "audio",
        "prompt_extend",
        "watermark",
        "seed",
    ]
    side_effects = [
        "writes video file to output_path",
        "calls DashScope (Alibaba Cloud) Wan video generation API",
    ]
    user_visible_verification = [
        "Inspect generated clip motion against the locked first frame and shot prompt"
    ]

    SUBMIT_URL = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "video-generation/video-synthesis"
    )
    POLL_URL_TEMPLATE = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

    # 北京区公开标价（USD / 秒）；以控制台为准。
    _COST_PER_SECOND = {
        "480P": 0.0636,
        "720P": 0.1272,
        "1080P": 0.2544,
    }

    def get_status(self) -> ToolStatus:
        if os.environ.get("DASHSCOPE_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        resolution = str(inputs.get("resolution", "480P"))
        duration = int(inputs.get("duration", 5))
        return round(self._COST_PER_SECOND.get(resolution, 0.0636) * duration, 4)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                error="DASHSCOPE_API_KEY not set. " + self.install_instructions,
            )

        start = time.time()
        try:
            payload = self._build_payload(inputs)
            result = self._generate(inputs, api_key=api_key, payload=payload)
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"DashScope Wan video failed: {self._safe_error(exc)}",
            )

        result.duration_seconds = round(time.time() - start, 2)
        return result

    def _generate(
        self,
        inputs: dict[str, Any],
        *,
        api_key: str,
        payload: dict[str, Any],
    ) -> ToolResult:
        import requests

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        submit_resp = requests.post(
            self.SUBMIT_URL, headers=headers, json=payload, timeout=(10, 120)
        )
        submit_data = self._json_or_raise(submit_resp)
        self._raise_for_error(submit_resp.status_code, submit_data)

        task_id = submit_data.get("output", {}).get("task_id")
        if not task_id:
            raise RuntimeError(
                "DashScope Wan submit succeeded but did not return output.task_id"
            )

        poll_data = self._poll_task(
            requests_module=requests,
            api_key=api_key,
            task_id=task_id,
            poll_interval=float(inputs.get("poll_interval_seconds", 5.0)),
            timeout_seconds=int(inputs.get("timeout_seconds", 900)),
        )

        video_url = poll_data.get("output", {}).get("video_url")
        if not video_url:
            raise RuntimeError(
                "DashScope Wan task succeeded but output.video_url missing"
            )

        output_path = Path(
            inputs.get("output_path", f"dashscope_wan_{task_id}.mp4")
        )
        self._download_video(video_url, output_path)

        usage = poll_data.get("usage", {})
        return ToolResult(
            success=True,
            data={
                "provider": "dashscope",
                "model": payload["model"],
                "operation": inputs.get("operation", "text_to_video"),
                "prompt": inputs["prompt"],
                "task_id": task_id,
                "video_url": video_url,
                "output": str(output_path),
                "resolution": payload["parameters"].get("resolution"),
                "ratio": payload["parameters"].get("ratio"),
                "requested_duration": payload["parameters"].get("duration"),
                "audio": payload["parameters"].get("audio"),
                "usage": usage,
                "fps": usage.get("fps"),
                "output_ratio": usage.get("ratio"),
            },
            artifacts=[str(output_path)],
            cost_usd=self.estimate_cost(inputs),
            model=payload["model"],
        )

    def _build_payload(self, inputs: dict[str, Any]) -> dict[str, Any]:
        operation = inputs.get("operation", "text_to_video")
        media = self._build_media(inputs, operation=operation)

        parameters: dict[str, Any] = {
            "resolution": inputs.get("resolution", "480P"),
            "ratio": inputs.get("ratio", "adaptive"),
            "duration": int(inputs.get("duration", 5)),
            "audio": bool(inputs.get("audio", False)),
            "prompt_extend": bool(inputs.get("prompt_extend", False)),
            "watermark": bool(inputs.get("watermark", False)),
        }
        if inputs.get("seed") is not None:
            parameters["seed"] = int(inputs["seed"])

        body_input: dict[str, Any] = {"prompt": inputs["prompt"]}
        if media:
            body_input["media"] = media

        return {
            "model": inputs.get("model", "wan3.0-video-prime"),
            "input": body_input,
            "parameters": parameters,
        }

    def _build_media(
        self, inputs: dict[str, Any], *, operation: str
    ) -> list[dict[str, str]]:
        if operation == "text_to_video":
            if any(
                inputs.get(k)
                for k in (
                    "image_path",
                    "image_url",
                    "last_frame_path",
                    "last_frame_url",
                )
            ):
                raise ValueError(
                    "text_to_video must not include image_path/image_url/"
                    "last_frame_path/last_frame_url"
                )
            return []

        if operation != "image_to_video":
            raise ValueError(f"Unsupported operation: {operation}")

        first = self._resolve_media_url(
            path=inputs.get("image_path"),
            url=inputs.get("image_url"),
            label="first_frame",
        )
        if not first:
            raise ValueError(
                "image_to_video requires image_path or image_url as first_frame"
            )

        media = [{"type": "first_frame", "url": first}]
        last = self._resolve_media_url(
            path=inputs.get("last_frame_path"),
            url=inputs.get("last_frame_url"),
            label="last_frame",
            required=False,
        )
        if last:
            media.append({"type": "last_frame", "url": last})
        return media

    def _resolve_media_url(
        self,
        *,
        path: str | None,
        url: str | None,
        label: str,
        required: bool = True,
    ) -> str | None:
        if path and url:
            raise ValueError(f"{label}: provide only one of path or url")
        if url:
            return str(url)
        if path:
            return self._file_to_data_uri(Path(path), label=label)
        if required:
            return None
        return None

    @staticmethod
    def _file_to_data_uri(path: Path, *, label: str) -> str:
        if not path.is_file():
            raise FileNotFoundError(f"{label} file not found: {path}")
        raw = path.read_bytes()
        if not raw:
            raise ValueError(f"{label} file is empty: {path}")
        # 约 15MB 原始上限，留余量给 base64 膨胀。
        if len(raw) > 15 * 1024 * 1024:
            raise ValueError(f"{label} file exceeds 15MB: {path}")
        mime, _ = mimetypes.guess_type(str(path))
        if mime not in {"image/png", "image/jpeg", "image/webp", "image/bmp"}:
            mime = "image/png"
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _poll_task(
        self,
        *,
        requests_module: Any,
        api_key: str,
        task_id: str,
        poll_interval: float,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        headers = {"Authorization": f"Bearer {api_key}"}
        while time.time() < deadline:
            time.sleep(poll_interval)
            resp = requests_module.get(
                self.POLL_URL_TEMPLATE.format(task_id=task_id),
                headers=headers,
                timeout=(10, 60),
            )
            data = self._json_or_raise(resp)
            self._raise_for_error(resp.status_code, data)
            status = data.get("output", {}).get("task_status")
            if status == "SUCCEEDED":
                return data
            if status == "FAILED":
                msg = data.get("output", {}).get("message") or data.get(
                    "message", "unknown error"
                )
                raise RuntimeError(f"DashScope Wan task failed: {msg}")
            if status == "CANCELED":
                raise RuntimeError(f"DashScope Wan task canceled: {task_id}")
        raise TimeoutError(
            f"DashScope Wan task {task_id} did not finish within "
            f"{timeout_seconds}s"
        )

    @staticmethod
    def _download_video(video_url: str, output_path: Path) -> None:
        import requests

        response = requests.get(video_url, timeout=180)
        response.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        partial = output_path.with_name(output_path.name + ".part")
        partial.write_bytes(response.content)
        partial.replace(output_path)

    @staticmethod
    def _json_or_raise(resp: Any) -> dict[str, Any]:
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(
                f"DashScope returned non-JSON (HTTP {resp.status_code})"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError("DashScope returned a non-object JSON body")
        return data

    @staticmethod
    def _raise_for_error(status_code: int, data: dict[str, Any]) -> None:
        if 200 <= status_code < 300:
            # 异步提交成功也可能在 body 里带 code。
            if data.get("code") and data.get("code") not in {"", "Success"}:
                raise RuntimeError(
                    f"DashScope error {data.get('code')}: {data.get('message')}"
                )
            return
        code = data.get("code", status_code)
        message = data.get("message", "unknown error")
        raise RuntimeError(f"DashScope HTTP {status_code} {code}: {message}")

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return str(exc).replace(
            os.environ.get("DASHSCOPE_API_KEY", ""), "[redacted]"
        )
