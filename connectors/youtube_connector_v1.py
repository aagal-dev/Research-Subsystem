import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
import requests

from .base_connector import BaseConnector


load_dotenv()

class YouTubeConnector(BaseConnector):
    """
    Research connector for YouTube transcript retrieval.

    Contract:
        execute(query: str) -> dict

    Pipeline:
        query
          -> search_videos()
          -> select top 2 videos
          -> fetch transcripts in parallel
          -> clean transcripts
          -> return LLM-consumable structured output
    """

    BASE_URL = "https://transcriptapi.com/api/v2"
    MAX_VIDEOS = 2
    DEFAULT_TIMEOUT = 20
    MAX_TRANSCRIPT_CHARS = 30_000

    @property
    def name(self):
        return "youtube_transcripts"

    def __init__(self, api_key=None, timeout=DEFAULT_TIMEOUT):
        self.api_key = api_key or os.getenv("TRANSCRIPTAPI_KEY")
        self.timeout = timeout

    def execute(self, query):
        """Run the complete YouTube research connector pipeline."""
        if not isinstance(query, str) or not query.strip():
            return self._error_result(
                query,
                code="invalid_query",
                message="Query must be a non-empty string.",
            )

        if not self.api_key:
            return self._error_result(
                query,
                code="missing_api_key",
                message="TranscriptAPI key is not configured.",
            )

        query = query.strip()

        try:
            videos = self.search_videos(query)
        except Exception as exc:
            return self._error_result(
                query,
                code="search_failed",
                message=str(exc),
            )

        selected_videos = videos[: self.MAX_VIDEOS]

        if not selected_videos:
            return {
                "schema_version": "1.0",
                "connector": self.name,
                "query": query,
                "status": "success",
                "results": [],
                "summary": {
                    "videos_found": 0,
                    "transcripts_succeeded": 0,
                    "transcripts_failed": 0,
                },
            }

        transcript_results = self._fetch_transcripts_parallel(selected_videos)

        results = []
        failed = []

        for video, transcript_result in zip(selected_videos, transcript_results):
            if transcript_result["status"] == "success":
                results.append(
                    self._structure_video_result(video, transcript_result)
                )
            else:
                failed.append(
                    {
                        "video_id": video["video_id"],
                        "title": video.get("title"),
                        "error": transcript_result["error"],
                    }
                )

        status = "success"
        if not results and failed:
            status = "partial_failure"

        return {
            "schema_version": "1.0",
            "connector": self.name,
            "query": query,
            "status": status,
            "results": results,
            "summary": {
                "videos_found": len(selected_videos),
                "transcripts_succeeded": len(results),
                "transcripts_failed": len(failed),
            },
            "errors": failed,
        }

    def search_videos(self, query):
        """Search YouTube and return normalized video metadata."""
        response = requests.get(
            f"{self.BASE_URL}/youtube/search",
            params={
                "q": query,
                "type": "video",
                "limit": self.MAX_VIDEOS,
            },
            headers=self._headers(),
            timeout=self.timeout,
        )

        response.raise_for_status()
        data = response.json()

        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raise ValueError("TranscriptAPI returned an invalid search response.")

        videos = []

        for item in raw_results:
            if not isinstance(item, dict):
                continue

            video_id = item.get("videoId") or item.get("video_id")
            if not video_id:
                continue

            videos.append(
                {
                    "video_id": video_id,
                    "title": item.get("title"),
                    "channel": (
                        item.get("channelName")
                        or item.get("channel")
                        or item.get("author")
                    ),
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "duration": item.get("duration"),
                    "view_count": (
                        item.get("viewCount")
                        or item.get("view_count")
                    ),
                    "published": item.get("published"),
                    "has_captions": item.get(
                        "hasCaptions",
                        item.get("has_captions"),
                    ),
                }
            )

        return videos[: self.MAX_VIDEOS]

    def get_transcript(self, video_id):
        """Fetch and clean one video's transcript."""
        response = requests.get(
            f"{self.BASE_URL}/youtube/transcript",
            params={
                "video_url": video_id,
                "format": "json",
                "include_timestamp": False,
                "send_metadata": False,
            },
            headers=self._headers(),
            timeout=self.timeout,
        )

        response.raise_for_status()
        data = response.json()

        raw_transcript = data.get("transcript", [])
        if not isinstance(raw_transcript, list):
            raise ValueError("TranscriptAPI returned an invalid transcript.")

        cleaned_segments = []

        for segment in raw_transcript:
            if not isinstance(segment, dict):
                continue

            text = self._clean_text(segment.get("text", ""))
            if text:
                cleaned_segments.append(text)

        transcript = self._join_transcript(cleaned_segments)

        if not transcript:
            raise ValueError("Transcript was empty after cleaning.")

        return {
            "language": data.get("language"),
            "transcript": transcript,
            "length_seconds": data.get("length_seconds"),
            "length_text": data.get("lengthText"),
        }

    def _fetch_transcripts_parallel(self, videos):
        results = [None] * len(videos)

        with ThreadPoolExecutor(max_workers=len(videos)) as executor:
            future_map = {
                executor.submit(
                    self.get_transcript,
                    video["video_id"],
                ): index
                for index, video in enumerate(videos)
            }

            for future in as_completed(future_map):
                index = future_map[future]

                try:
                    transcript = future.result()
                    results[index] = {
                        "status": "success",
                        **transcript,
                    }
                except requests.HTTPError as exc:
                    results[index] = {
                        "status": "error",
                        "error": self._format_http_error(exc),
                    }
                except requests.RequestException as exc:
                    results[index] = {
                        "status": "error",
                        "error": f"network_error: {exc}",
                    }
                except Exception as exc:
                    results[index] = {
                        "status": "error",
                        "error": str(exc),
                    }

        return results

    def _structure_video_result(self, video, transcript_result):
        transcript = transcript_result["transcript"]

        truncated = len(transcript) > self.MAX_TRANSCRIPT_CHARS
        if truncated:
            transcript = transcript[: self.MAX_TRANSCRIPT_CHARS].rstrip() + "..."

        return {
            "video": {
                "video_id": video["video_id"],
                "title": video.get("title"),
                "channel": video.get("channel"),
                "url": video["url"],
                "duration": video.get("duration"),
                "view_count": video.get("view_count"),
                "published": video.get("published"),
                "has_captions": video.get("has_captions"),
            },
            "transcript": {
                "language": transcript_result.get("language"),
                "text": transcript,
                "length_seconds": transcript_result.get("length_seconds"),
                "length_text": transcript_result.get("length_text"),
                "truncated": truncated,
            },
        }

    @staticmethod
    def _clean_text(text):
        if not isinstance(text, str):
            return ""

        text = " ".join(text.split())
        return text.strip()

    @staticmethod
    def _join_transcript(segments):
        """Remove repeated adjacent caption segments and join into readable text."""
        cleaned = []
        previous = None

        for segment in segments:
            if segment == previous:
                continue

            cleaned.append(segment)
            previous = segment

        return " ".join(cleaned)

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    @staticmethod
    def _format_http_error(exc):
        response = getattr(exc, "response", None)

        if response is None:
            return f"http_error: {exc}"

        try:
            data = response.json()
            detail = (
                data.get("detail")
                or data.get("message")
                or data.get("error")
            )
        except ValueError:
            detail = None

        if detail:
            return f"http_{response.status_code}: {detail}"

        return f"http_{response.status_code}: {response.reason}"

    def _error_result(self, query, code, message):
        return {
            "schema_version": "1.0",
            "connector": self.name,
            "query": query,
            "status": "error",
            "results": [],
            "summary": {
                "videos_found": 0,
                "transcripts_succeeded": 0,
                "transcripts_failed": 0,
            },
            "errors": [
                {
                    "code": code,
                    "message": message,
                }
            ],
        }
