from __future__ import annotations

import shutil
import subprocess
import tempfile
import json
from pathlib import Path
from typing import Iterable

from ffmpy import FFmpeg, FFRuntimeError

from services.platform.proxy_service import proxy_settings
from services.video.composition.models import TimelineAudioClip, TimelineDocument, TimelineSubtitleCue, TimelineVideoClip
from services.video.composition.storage import VideoCompositionStorage


class VideoCompositionError(RuntimeError):
    pass


def _number(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def _linear_expression(start: float, end: float, duration: float) -> str:
    if abs(start - end) < 0.0001:
        return _number(start)
    progress = f"min(max(t/{_number(max(0.05, duration))},0),1)"
    return f"({_number(start)}+({_number(end - start)})*{progress})"


def _escape_filter_path(path: Path) -> str:
    # FFmpeg filter options use ':' as a separator on Windows.
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _write_srt(cues: Iterable[TimelineSubtitleCue], path: Path, total_duration: float) -> bool:
    lines: list[str] = []
    for index, cue in enumerate(cues, start=1):
        start = max(0.0, min(cue.start, total_duration))
        end = max(start + 0.01, min(cue.end, total_duration))
        if start >= total_duration or not cue.text.strip():
            continue

        def timestamp(value: float) -> str:
            millis = int(round(value * 1000))
            hours, remainder = divmod(millis, 3_600_000)
            minutes, remainder = divmod(remainder, 60_000)
            seconds, milliseconds = divmod(remainder, 1000)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

        lines.extend([str(index), f"{timestamp(start)} --> {timestamp(end)}", cue.text.strip(), ""])
    if not lines:
        return False
    path.write_text("\n".join(lines), encoding="utf-8-sig")
    return True


class VideoCompositionRenderer:
    def __init__(self, storage: VideoCompositionStorage):
        self.storage = storage

    def _stage_source(self, source_url: str, work_dir: Path, name: str) -> Path:
        managed = self.storage.managed_source(source_url)
        target = work_dir / name
        if managed is not None:
            shutil.copyfile(managed, target)
            return target

        # Remote results are staged locally before FFmpeg sees them. This keeps
        # the renderer deterministic and lets the storage layer enforce limits.
        from curl_cffi import requests

        try:
            response = requests.get(
                source_url,
                headers={"Accept": "video/*,audio/*,*/*;q=0.8", "User-Agent": "gmkraw video composition"},
                timeout=180,
                allow_redirects=True,
                **proxy_settings.build_session_kwargs(),
            )
        except Exception as exc:
            raise VideoCompositionError(f"下载媒体失败：{exc}") from exc
        if not 200 <= response.status_code < 300:
            raise VideoCompositionError(f"下载媒体失败：HTTP {response.status_code}")
        payload = bytes(response.content)
        if not payload:
            raise VideoCompositionError("下载媒体为空")
        if len(payload) > self.storage.settings.max_source_mb * 1024 * 1024:
            raise VideoCompositionError(f"媒体文件不能超过 {self.storage.settings.max_source_mb} MB")
        target.write_bytes(payload)
        return target

    def _run(self, inputs: dict[str, list[str]], output: Path, options: list[str]) -> None:
        command = FFmpeg(
            executable=self.storage.settings.ffmpeg_path,
            global_options=["-y", "-hide_banner", "-loglevel", "error"],
            inputs=inputs,
            outputs={str(output): options},
        )
        try:
            command.run(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except (FFRuntimeError, OSError) as exc:
            detail = getattr(exc, "stderr", None)
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            raise VideoCompositionError(str(detail or exc).strip()[-4000:]) from exc

    def _source_has_audio(self, path: Path) -> bool:
        try:
            result = subprocess.run(
                [
                    self.storage.settings.ffprobe_path,
                    "-v", "error",
                    "-select_streams", "a:0",
                    "-show_entries", "stream=index",
                    "-of", "json",
                    str(path),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            data = json.loads(result.stdout.decode("utf-8", errors="replace") or "{}")
            return bool(data.get("streams"))
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return False

    @staticmethod
    def _video_filter(
        clips: list[TimelineVideoClip],
        width: int,
        height: int,
        fps: int,
        has_audio: list[bool],
        total_duration: float,
        *,
        fit: str,
        background_color: str,
    ) -> tuple[str, str, str]:
        prepared: list[str] = []
        color = f"0x{background_color.lstrip('#')}"
        for index, clip in enumerate(clips):
            if fit == "cover":
                frame_filter = (
                    f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height}"
                )
            else:
                frame_filter = (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={color}"
                )
            start_keyframe = clip.keyframes.start
            end_keyframe = clip.keyframes.end
            zoom = _linear_expression(start_keyframe.scale, end_keyframe.scale, clip.duration)
            pan_x = _linear_expression(start_keyframe.x, end_keyframe.x, clip.duration)
            pan_y = _linear_expression(start_keyframe.y, end_keyframe.y, clip.duration)
            rotation = _linear_expression(start_keyframe.rotation, end_keyframe.rotation, clip.duration)
            brightness = _linear_expression(start_keyframe.brightness, end_keyframe.brightness, clip.duration)
            contrast = _linear_expression(start_keyframe.contrast, end_keyframe.contrast, clip.duration)
            saturation = _linear_expression(start_keyframe.saturation, end_keyframe.saturation, clip.duration)
            visual_filter = (
                f"eq=brightness='{brightness}':contrast='{contrast}':saturation='{saturation}':eval=frame,"
                f"rotate='PI/180*({rotation})':c={color}:ow=iw:oh=ih,"
                f"scale=w='trunc(iw*({zoom})/2)*2':h='trunc(ih*({zoom})/2)*2':eval=frame,"
                f"crop={width}:{height}:"
                f"x='max(0,(iw-ow)/2*(1+({pan_x})))':"
                f"y='max(0,(ih-oh)/2*(1+({pan_y})))'"
            )
            if clip.track == 0 or (clip.timeline_start < total_duration and clip.opacity > 0):
                prepared.append(
                    f"[{index}:v]{frame_filter},{visual_filter},fps={fps},format=yuv420p,setsar=1,settb=AVTB,"
                    f"tpad=stop_mode=clone:stop_duration={_number(clip.duration)},"
                    f"trim=duration={_number(clip.duration)},setpts=PTS-STARTPTS[v{index}]"
                )
            if clip.track != 0:
                continue
            if has_audio[index] and not clip.muted and clip.volume > 0:
                prepared.append(
                    f"[{index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    f"apad=whole_dur={_number(clip.duration)},atrim=duration={_number(clip.duration)},"
                    f"volume={_number(clip.volume)},asetpts=PTS-STARTPTS[va{index}]"
                )
            else:
                prepared.append(
                    f"anullsrc=r=48000:cl=stereo:d={_number(clip.duration)},"
                    f"atrim=duration={_number(clip.duration)},asetpts=PTS-STARTPTS[va{index}]"
                )

        primary_indices = [index for index, clip in enumerate(clips) if clip.track == 0]
        groups: list[list[int]] = [[primary_indices[0]]]
        for index in primary_indices[1:]:
            if clips[index].transition.type == "fade":
                groups[-1].append(index)
            else:
                groups.append([index])

        group_labels: list[str] = []
        audio_group_labels: list[str] = []
        for group_index, group in enumerate(groups):
            if len(group) == 1:
                group_labels.append(f"v{group[0]}")
                audio_group_labels.append(f"va{group[0]}")
                continue
            current = f"v{group[0]}"
            current_audio = f"va{group[0]}"
            elapsed = clips[group[0]].duration
            for group_position, clip_index in enumerate(group[1:], start=1):
                previous_clip_index = group[group_position - 1]
                transition = min(
                    clips[clip_index].transition.duration,
                    clips[clip_index].duration / 2,
                    clips[previous_clip_index].duration / 2,
                )
                output_label = f"xfade{group_index}_{clip_index}"
                prepared.append(
                    f"[{current}][v{clip_index}]xfade=transition=fade:duration={_number(transition)}:"
                    f"offset={_number(max(0.01, elapsed - transition))}[{output_label}]"
                )
                audio_output_label = f"across{group_index}_{clip_index}"
                prepared.append(
                    f"[{current_audio}][va{clip_index}]acrossfade=d={_number(transition)}:c1=tri:c2=tri"
                    f"[{audio_output_label}]"
                )
                current = output_label
                current_audio = audio_output_label
                elapsed += clips[clip_index].duration - transition
            group_labels.append(current)
            audio_group_labels.append(current_audio)

        if len(group_labels) == 1:
            video_label = group_labels[0]
            primary_audio_label = audio_group_labels[0]
        else:
            concat_inputs = "".join(f"[{label}]" for label in group_labels)
            prepared.append(f"{concat_inputs}concat=n={len(group_labels)}:v=1:a=0[vprimary]")
            audio_concat_inputs = "".join(f"[{label}]" for label in audio_group_labels)
            prepared.append(f"{audio_concat_inputs}concat=n={len(audio_group_labels)}:v=0:a=1[aprimary]")
            video_label = "vprimary"
            primary_audio_label = "aprimary"

        overlays = sorted(
            (
                (index, clip)
                for index, clip in enumerate(clips)
                if clip.track > 0 and clip.timeline_start < total_duration
            ),
            key=lambda item: (item[1].track, item[1].timeline_start, item[0]),
        )
        overlay_audio_labels: list[str] = []
        for layer_index, (input_index, clip) in enumerate(overlays):
            start = max(0.0, clip.timeline_start)
            effective_duration = min(clip.duration, max(0.0, total_duration - start))
            if effective_duration <= 0:
                continue

            if clip.opacity > 0:
                overlay_label = f"overlay{layer_index}"
                composite_label = f"vcomposite{layer_index}"
                end = start + effective_duration
                prepared.append(
                    f"[v{input_index}]trim=duration={_number(effective_duration)},"
                    f"format=rgba,colorchannelmixer=aa={_number(clip.opacity)},"
                    f"setpts=PTS-STARTPTS+{_number(start)}/TB[{overlay_label}]"
                )
                prepared.append(
                    f"[{video_label}][{overlay_label}]overlay=x=0:y=0:eof_action=pass:repeatlast=0:"
                    f"shortest=0:format=auto:enable='between(t,{_number(start)},{_number(end)})'"
                    f"[{composite_label}]"
                )
                video_label = composite_label

            if has_audio[input_index] and not clip.muted and clip.volume > 0:
                audio_label = f"overlayaudio{layer_index}"
                delay_ms = max(0, int(round(start * 1000)))
                delay = f",adelay={delay_ms}|{delay_ms}" if delay_ms else ""
                prepared.append(
                    f"[{input_index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    f"apad=whole_dur={_number(effective_duration)},atrim=duration={_number(effective_duration)},"
                    f"volume={_number(clip.volume)},asetpts=PTS-STARTPTS{delay}[{audio_label}]"
                )
                overlay_audio_labels.append(audio_label)

        if overlay_audio_labels:
            source_audio_labels = [primary_audio_label, *overlay_audio_labels]
            mix_inputs = "".join(f"[{label}]" for label in source_audio_labels)
            prepared.append(
                f"{mix_inputs}amix=inputs={len(source_audio_labels)}:duration=longest:"
                f"dropout_transition=0:normalize=0,alimiter=limit=0.98,"
                f"atrim=duration={_number(total_duration)},asetpts=PTS-STARTPTS[avideo]"
            )
            source_audio_label = "avideo"
        else:
            source_audio_label = primary_audio_label

        return ";".join(prepared), video_label, source_audio_label

    @staticmethod
    def _audio_filter(
        clips: list[TimelineAudioClip],
        video_input_count: int,
        total_duration: float,
    ) -> tuple[str, str]:
        filters: list[str] = []
        labels: list[str] = []
        for offset, clip in enumerate(clips):
            index = video_input_count + offset
            label = f"audio{offset}"
            delay_ms = max(0, int(round(clip.start * 1000)))
            parts = [
                f"[{index}:a]atrim=start={_number(clip.source_start)}:duration={_number(clip.duration)}",
                "asetpts=PTS-STARTPTS",
                f"volume={_number(clip.volume)}",
            ]
            if clip.fade_in > 0:
                parts.append(f"afade=t=in:st=0:d={_number(clip.fade_in)}")
            if clip.fade_out > 0:
                fade_start = max(0.0, clip.duration - clip.fade_out)
                parts.append(f"afade=t=out:st={_number(fade_start)}:d={_number(clip.fade_out)}")
            if delay_ms:
                parts.append(f"adelay={delay_ms}|{delay_ms}")
            # Keep the expression explicit; it is easier to audit than a
            # nested builder and avoids shell-level quoting surprises.
            source = f"[{index}:a]atrim=start={_number(clip.source_start)}:duration={_number(clip.duration)},"
            source += ",".join(parts[1:])
            filters.append(f"{source}[{label}]")
            labels.append(label)

        joined = ";".join(filters)
        if len(labels) == 1:
            return joined, labels[0]
        joined += ";" + "".join(f"[{label}]" for label in labels)
        joined += f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0," \
            f"atrim=duration={_number(total_duration)},asetpts=PTS-STARTPTS[aout]"
        return joined, "aout"

    def render(self, timeline: TimelineDocument, *, owner_id: str, task_id: str, base_url: str) -> dict[str, object]:
        width, height = timeline.output.dimensions()
        total_duration = timeline.duration()
        with tempfile.TemporaryDirectory(prefix="gmkraw-composition-") as temporary:
            work_dir = Path(temporary)
            video_inputs: dict[str, list[str]] = {}
            staged_videos: list[Path] = []
            for index, clip in enumerate(timeline.video_clips):
                staged = self._stage_source(clip.source_url, work_dir, f"video-{index}.source")
                staged_videos.append(staged)
                options: list[str] = []
                if clip.source_start > 0:
                    options.extend(["-ss", _number(clip.source_start)])
                options.extend(["-t", _number(clip.duration)])
                video_inputs[str(staged)] = options

            video_audio_streams = [self._source_has_audio(path) for path in staged_videos]

            staged_audio: list[Path] = []
            audio_inputs: dict[str, list[str]] = {}
            for index, clip in enumerate(timeline.audio_clips):
                staged = self._stage_source(clip.source_url, work_dir, f"audio-{index}.source")
                staged_audio.append(staged)
                options: list[str] = ["-stream_loop", "-1"] if clip.loop else []
                audio_inputs[str(staged)] = options

            all_inputs = {**video_inputs, **audio_inputs}
            video_filter, video_label, source_audio_label = self._video_filter(
                timeline.video_clips,
                width,
                height,
                timeline.output.fps,
                video_audio_streams,
                total_duration,
                fit=timeline.output.fit,
                background_color=timeline.output.background_color,
            )
            filter_parts = [video_filter]
            audio_label = source_audio_label
            if timeline.audio_clips:
                audio_filter, external_audio_label = self._audio_filter(
                    timeline.audio_clips,
                    len(staged_videos),
                    total_duration,
                )
                filter_parts.append(audio_filter)
                filter_parts.append(
                    f"[{source_audio_label}][{external_audio_label}]"
                    f"amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
                    f"alimiter=limit=0.98,atrim=duration={_number(total_duration)}[amixed]"
                )
                audio_label = "amixed"

            subtitle_path = work_dir / "subtitles.srt"
            if timeline.output.burn_subtitles and _write_srt(timeline.subtitles, subtitle_path, total_duration):
                escaped = _escape_filter_path(subtitle_path)
                subtitle_label = "vwithsubs"
                filter_parts.append(f"[{video_label}]subtitles='{escaped}'[{subtitle_label}]")
                video_label = subtitle_label

            target, relative_path, url = self.storage.result_target(
                owner_id=owner_id,
                task_id=task_id,
                base_url=base_url,
            )
            output_options = [
                "-filter_complex", ";".join(filter_parts),
                "-map", f"[{video_label}]",
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-r", str(timeline.output.fps),
                "-movflags", "+faststart",
                "-t", _number(total_duration),
            ]
            output_options.extend([
                "-map", f"[{audio_label}]",
                "-c:a", "aac",
                "-b:a", "192k",
            ])
            self._run(all_inputs, target, output_options)
            if not target.is_file() or target.stat().st_size == 0:
                raise VideoCompositionError("视频合成完成但没有输出文件")
            return {
                "result_url": url,
                "resultUrl": url,
                "storage_rel": relative_path,
                "duration": total_duration,
                "width": width,
                "height": height,
                "size": target.stat().st_size,
            }
