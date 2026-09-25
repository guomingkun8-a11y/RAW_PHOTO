from __future__ import annotations

import unittest
from unittest import mock

from services.video import video_generation_provider as provider


class VideoGenerationProviderTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "enabled": True,
            "base_url": "https://api.example.test/v1",
            "api_keys": ["key-one", "key-two"],
            "submit_path": "/v1/media/generate",
            "status_path": "/v1/media/status",
            "poll_timeout_secs": 30,
            "poll_interval_secs": 3,
        }
        self.settings_patcher = mock.patch.object(provider, "_settings", return_value=self.settings)
        self.settings_patcher.start()
        self.addCleanup(self.settings_patcher.stop)
        self.now = 1000.0
        self.body = {"model": "hailuo-h3", "prompt": "a product shot", "params": {}}
        self.credential_id = provider._credential_id("key-two")
        self.resume = {
            "upstream_task_id": "upstream-1",
            "credential_id": self.credential_id,
            "submission_state": "submitted",
            "previous_cost": 1.0,
            "poll_started_ts": self.now - 3,
        }
        self.success_payload = {"status": "success", "video_url": "https://cdn.example.test/video.mp4"}
        patchers = {
            "post": mock.patch.object(provider.requests, "post", return_value=self.response({"task_id": "upstream-1"})),
            "get": mock.patch.object(provider.requests, "get", return_value=self.response(self.success_payload)),
            "sleep": mock.patch.object(provider.time, "sleep", side_effect=self.advance_time),
            "time": mock.patch.object(provider.time, "time", side_effect=lambda: self.now),
            "monotonic": mock.patch.object(provider.time, "monotonic", side_effect=lambda: self.now),
            "proxy": mock.patch.object(provider.proxy_settings, "build_session_kwargs", return_value={}),
        }
        for name, patcher in patchers.items():
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        with provider._API_KEY_LOCK:
            provider._API_KEY_INDEX = 0

    @staticmethod
    def response(data, status_code=200):
        response = mock.Mock(status_code=status_code)
        response.json.return_value = data
        return response

    def advance_time(self, seconds):
        self.now += seconds

    def test_submit_uses_model_specific_params(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"task_id": "upstream-1"}
        with mock.patch.object(provider.requests, "post", return_value=response) as post:
            result = provider.submit_video_generation({
                "model": "doubao-seedance-2-5-cankaosheng",
                "prompt": "a lake at sunrise",
                "image_urls": ["https://cdn.example.test/reference.png"],
                "params": {
                    "aspect_ratio": "16:9",
                    "duration": "auto",
                    "resolution": "480p",
                    "web_search": False,
                },
            })

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "doubao-seedance-2-5-cankaosheng")
        self.assertEqual(payload["params"]["resolution"], "480p")
        self.assertFalse(payload["params"]["web_search"])
        self.assertEqual(payload["params"]["images"], ["https://cdn.example.test/reference.png"])
        self.assertNotIn("images", payload)
        self.assertEqual(result["upstream_task_id"], "upstream-1")
        self.assertEqual(result["credential_id"], provider._credential_id("key-one"))
        self.assertNotIn("api_key", result)
        self.assertNotIn("key-one", repr(result))

    def test_submit_preserves_first_then_last_frame_order(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"task_id": "upstream-frames"}
        with mock.patch.object(provider.requests, "post", return_value=response) as post:
            provider.submit_video_generation({
                "model": "hailuo-h3-max-shouweizhen",
                "prompt": "move from opening frame to closing frame",
                "image_urls": [
                    "https://cdn.example.test/first.png",
                    "https://cdn.example.test/last.png",
                ],
                "params": {
                    "duration": "10",
                    "resolution": "768P",
                },
            })

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["params"], {
            "duration": "10",
            "resolution": "768P",
            "images": [
                "https://cdn.example.test/first.png",
                "https://cdn.example.test/last.png",
            ],
        })

    def test_status_reuses_submission_key(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "status": "success",
            "video_url": "https://cdn.example.test/video.mp4",
        }
        with mock.patch.object(provider.requests, "get", return_value=response) as get:
            result = provider.get_video_generation_status("upstream-1", self.credential_id)

        self.assertTrue(result["finished"])
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer key-two")

    def test_status_recognizes_chinese_failure_and_uses_upstream_error(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "is_final": True,
            "progress": "100",
            "status": "失败",
            "state": "failed",
            "error": "reference image was rejected",
        }
        with mock.patch.object(provider.requests, "get", return_value=response):
            result = provider.get_video_generation_status("upstream-failed", provider._credential_id("key-one"))

        self.assertTrue(result["finished"])
        self.assertTrue(result["failed"])
        self.assertEqual(result["error"], "reference image was rejected")

    def test_run_waits_for_result_url_after_final_status(self):
        submission_callback = mock.Mock()
        progress = []
        statuses = [
            {"finished": True, "failed": False, "video_url": "", "raw": {"is_final": True}},
            {
                "finished": True,
                "failed": False,
                "video_url": "https://cdn.example.test/video.mp4",
                "cover_url": "",
                "cost": 1.25,
                "raw": {"is_final": True, "result_url": "https://cdn.example.test/video.mp4"},
            },
        ]
        with (
            mock.patch.object(provider.requests, "post", return_value=self.response({"task_id": "upstream-delayed-url"})),
            mock.patch.object(provider, "get_video_generation_status", side_effect=statuses) as get_status,
            mock.patch.object(provider.time, "sleep"),
        ):
            result = provider.run_video_generation({
                **self.body,
                "submission_callback": submission_callback,
                "progress_callback": progress.append,
            })

        submission_callback.assert_called_once_with(
            "upstream-delayed-url", credential_id=provider._credential_id("key-one"), cost=None,
        )
        self.assertIn("finalizing", progress)
        self.assertEqual(get_status.call_count, 2)
        self.assertEqual(result["video_url"], "https://cdn.example.test/video.mp4")

    def test_submission_keys_rotate(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"task_id": "upstream-1"}
        with mock.patch.object(provider.requests, "post", return_value=response) as post:
            provider.submit_video_generation({
                "model": "hailuo-h3",
                "prompt": "a product shot",
                "params": {},
            })
            provider.submit_video_generation({
                "model": "kling-v3-video",
                "prompt": "a product shot",
                "params": {},
            })

        headers = [call.kwargs["headers"]["Authorization"] for call in post.call_args_list]
        self.assertEqual(headers, ["Bearer key-one", "Bearer key-two"])

    def test_error_defaults_are_permanent_and_carry_context(self):
        error = provider.VideoGenerationProviderError("invalid model")
        self.assertIsInstance(error, RuntimeError)
        self.assertFalse(error.retryable)
        self.assertFalse(error.submission_uncertain)
        self.assertFalse(error.upstream_finished)
        self.assertIsNone(error.cost)
        self.assertEqual(error.upstream_task_id, "")
        self.assertEqual(error.credential_id, "")

    def test_submission_is_persisted_before_first_poll_without_exposing_secret(self):
        events = []
        self.post.side_effect = lambda *args, **kwargs: (
            events.append("post") or self.response({"task_id": "upstream-1", "cost": "1.25"})
        )
        self.get.side_effect = lambda *args, **kwargs: events.append("get") or self.response(self.success_payload)

        def persist(upstream_task_id, credential_id="", cost=None):
            events.append("persisted")
            self.assertEqual(upstream_task_id, "upstream-1")
            self.assertEqual(credential_id, provider._credential_id("key-one"))
            self.assertEqual(cost, 1.25)

        result = provider.run_video_generation({
            **self.body,
            "submission_started_callback": lambda: events.append("started"),
            "submission_callback": persist,
        })

        self.assertEqual(events, ["started", "post", "persisted", "get"])
        self.assertEqual(result["cost"], 1.25)
        self.assertEqual(result["credential_id"], provider._credential_id("key-one"))
        self.assertNotIn("key-one", repr(result))
        self.assertFalse(self.post.call_args.kwargs["allow_redirects"])
        self.assertFalse(self.get.call_args.kwargs["allow_redirects"])

    def test_started_callback_fencing_or_failure_prevents_post(self):
        for callback in (mock.Mock(return_value=False), mock.Mock(side_effect=RuntimeError("lease lost"))):
            with self.subTest(callback=callback):
                with self.assertRaises(provider.VideoGenerationProviderError) as raised:
                    provider.run_video_generation({**self.body, "submission_started_callback": callback})
                self.assertFalse(raised.exception.retryable)
                self.assertFalse(raised.exception.submission_uncertain)
                callback.assert_called_once_with()
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_validation_runs_before_started_callback(self):
        callback = mock.Mock()
        for body in ({"prompt": "test"}, {"model": "hailuo-h3"}):
            with self.subTest(body=body), self.assertRaises(provider.VideoGenerationProviderError):
                provider.run_video_generation({**body, "submission_started_callback": callback})
        callback.assert_not_called()
        self.post.assert_not_called()

    def test_submission_persistence_failure_retains_task_and_stops_polling(self):
        for callback, retryable in (
            (mock.Mock(side_effect=RuntimeError("database unavailable")), True),
            (mock.Mock(return_value=False), False),
        ):
            with self.subTest(retryable=retryable):
                self.post.return_value = self.response({"task_id": "upstream-1", "cost": 0})
                with self.assertRaises(provider.VideoGenerationProviderError) as raised:
                    provider.run_video_generation({**self.body, "submission_callback": callback})
                error = raised.exception
                self.assertEqual(error.retryable, retryable)
                self.assertFalse(error.submission_uncertain)
                self.assertEqual(error.upstream_task_id, "upstream-1")
                self.assertTrue(error.credential_id.startswith("sha256:"))
                self.assertEqual(error.cost, 0)
        self.get.assert_not_called()

    def test_resume_only_polls_original_credential_after_pool_reordering(self):
        self.settings["api_keys"] = ["key-two", "key-one"]
        started, submitted = mock.Mock(), mock.Mock()
        result = provider.run_video_generation({
            **self.resume, "submission_started_callback": started, "submission_callback": submitted,
        })
        self.post.assert_not_called()
        started.assert_not_called()
        submitted.assert_not_called()
        self.assertEqual(self.get.call_args.kwargs["headers"]["Authorization"], "Bearer key-two")
        self.assertEqual(self.get.call_args.kwargs["params"], {"task_id": "upstream-1"})
        self.assertEqual(result["credential_id"], self.credential_id)
        self.assertEqual(result["cost"], 1.0)
        self.assertEqual(provider._API_KEY_INDEX, 0)

    def test_missing_original_credential_fails_without_rotating(self):
        self.settings["api_keys"] = ["key-one"]
        for credential_id in ("", self.credential_id, "unrecognized-fingerprint"):
            with self.subTest(credential_id=credential_id):
                with self.assertRaises(provider.VideoGenerationProviderError) as raised:
                    provider.run_video_generation({**self.resume, "credential_id": credential_id})
                error = raised.exception
                self.assertFalse(error.retryable)
                self.assertEqual(error.upstream_task_id, "upstream-1")
                self.assertEqual(error.credential_id, credential_id)
                self.assertEqual(error.cost, 1.0)
        self.post.assert_not_called()
        self.get.assert_not_called()
        self.assertEqual(provider._API_KEY_INDEX, 0)

    def test_status_rejects_missing_or_raw_credential(self):
        for credential_id in ("", "key-one"):
            with self.subTest(credential_id=credential_id):
                with self.assertRaises(provider.VideoGenerationProviderError) as raised:
                    provider.get_video_generation_status("upstream-1", credential_id)
                self.assertFalse(raised.exception.retryable)
                self.assertEqual(raised.exception.upstream_task_id, "upstream-1")
        self.get.assert_not_called()

    def test_uncertain_state_without_task_id_cannot_resubmit(self):
        started = mock.Mock()
        for state in ("submitting", "submitted", "uncertain", "submission_uncertain", "unknown-state"):
            with self.subTest(state=state):
                with self.assertRaises(provider.VideoGenerationProviderError) as raised:
                    provider.run_video_generation({
                        **self.body, "submission_state": state, "previous_cost": 0,
                        "submission_started_callback": started,
                    })
                self.assertFalse(raised.exception.retryable)
                self.assertTrue(raised.exception.submission_uncertain)
                self.assertEqual(raised.exception.cost, 0)
        started.assert_not_called()
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_known_task_is_polled_even_when_submission_state_is_submitting(self):
        result = provider.run_video_generation({**self.resume, "submission_state": "submitting"})
        self.assertEqual(result["upstream_task_id"], "upstream-1")
        self.post.assert_not_called()
        self.get.assert_called_once()

    def test_submit_helper_refuses_existing_task(self):
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.submit_video_generation({**self.body, **self.resume})
        self.assertEqual(raised.exception.upstream_task_id, "upstream-1")
        self.post.assert_not_called()

    def test_post_http_failures_are_classified_without_internal_retry(self):
        for code, retryable, uncertain in (
            (400, False, False), (401, False, False), (403, False, False),
            (429, True, False), (408, False, True), (500, False, True),
            (502, False, True), (503, False, True), (307, False, True),
        ):
            with self.subTest(code=code):
                self.post.reset_mock()
                self.post.return_value = self.response({"error": "upstream rejected request", "cost": "0.25"}, code)
                with self.assertRaises(provider.VideoGenerationProviderError) as raised:
                    provider.run_video_generation(self.body)
                error = raised.exception
                self.assertEqual(error.retryable, retryable)
                self.assertEqual(error.submission_uncertain, uncertain)
                self.assertEqual(error.cost, 0.25)
                self.assertTrue(error.credential_id.startswith("sha256:"))
                self.post.assert_called_once()
        self.get.assert_not_called()
        self.sleep.assert_not_called()

    def test_post_transport_failure_is_uncertain(self):
        for failure in (
            provider.requests.exceptions.Timeout("timed out with key-one"),
            provider.requests.exceptions.ConnectionError("connection reset"),
            OSError("socket closed"),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.post.reset_mock()
                self.post.side_effect = failure
                with self.assertRaises(provider.VideoGenerationProviderError) as raised:
                    provider.run_video_generation({**self.body, "previous_cost": 0})
                error = raised.exception
                self.assertFalse(error.retryable)
                self.assertTrue(error.submission_uncertain)
                self.assertEqual(error.cost, 0)
                self.assertNotIn("key-one", str(error))
                self.post.assert_called_once()
        self.get.assert_not_called()

    def test_accepted_post_without_usable_task_id_is_uncertain(self):
        for payload in (
            None, [], "invalid JSON", {}, {"cost": 1.5},
            {"task_id": True}, {"task_id": {"id": "not-a-task-id"}},
            {"data": {"video": {"id": "asset-id"}}},
        ):
            with self.subTest(payload=payload):
                self.post.return_value = self.response(payload)
                with self.assertRaises(provider.VideoGenerationProviderError) as raised:
                    provider.run_video_generation(self.body)
                self.assertTrue(raised.exception.submission_uncertain)
                self.assertFalse(raised.exception.retryable)
                if isinstance(payload, dict) and "cost" in payload:
                    self.assertEqual(raised.exception.cost, 1.5)
        self.get.assert_not_called()

    def test_task_id_is_preferred_over_envelope_id(self):
        self.post.return_value = self.response({"id": "request-id", "data": {"task_id": "real-task-id"}})
        result = provider.submit_video_generation(self.body)
        self.assertEqual(result["upstream_task_id"], "real-task-id")

    def test_post_failure_retains_known_upstream_id_and_cost(self):
        self.post.return_value = self.response({"data": {"task_id": "accepted-task", "cost": 2}}, 503)
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.run_video_generation(self.body)
        self.assertEqual(raised.exception.upstream_task_id, "accepted-task")
        self.assertEqual(raised.exception.cost, 2)
        self.assertFalse(raised.exception.submission_uncertain)
        self.assertTrue(raised.exception.retryable)

    def test_clear_429_rejection_can_be_scheduled_as_new_submission(self):
        started = mock.Mock()
        self.post.side_effect = [self.response({"error": "rate limited"}, 429), self.response({"task_id": "upstream-1"})]
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.run_video_generation({**self.body, "submission_started_callback": started})
        self.assertTrue(raised.exception.retryable)
        self.assertFalse(raised.exception.submission_uncertain)
        result = provider.run_video_generation({
            **self.body, "submission_state": "rejected", "submission_started_callback": started,
        })
        self.assertEqual(result["upstream_task_id"], "upstream-1")
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(started.call_count, 2)

    def test_poll_http_failures_are_delegated_to_scheduler(self):
        for code, retryable in ((400, False), (401, False), (404, False), (408, True), (429, True), (500, True), (503, True)):
            with self.subTest(code=code):
                self.get.reset_mock()
                self.get.return_value = self.response({"error": "status unavailable"}, code)
                with self.assertRaises(provider.VideoGenerationProviderError) as raised:
                    provider.run_video_generation(self.resume)
                error = raised.exception
                self.assertEqual(error.retryable, retryable)
                self.assertFalse(error.submission_uncertain)
                self.assertEqual(error.upstream_task_id, "upstream-1")
                self.assertEqual(error.credential_id, self.credential_id)
                self.assertEqual(error.cost, 1.0)
                self.get.assert_called_once()
        self.post.assert_not_called()
        self.sleep.assert_not_called()

    def test_transient_polling_failure_can_resume_without_second_post(self):
        submitted = mock.Mock()
        self.post.return_value = self.response({"task_id": "upstream-1", "cost": 2})
        self.get.side_effect = [
            provider.requests.exceptions.Timeout("read timeout"),
            self.response({**self.success_payload, "cost": 3}),
        ]
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.run_video_generation({**self.body, "submission_callback": submitted})
        error = raised.exception
        self.assertTrue(error.retryable)
        self.assertEqual(error.cost, 2)
        result = provider.run_video_generation({
            "upstream_task_id": error.upstream_task_id,
            "credential_id": error.credential_id,
            "previous_cost": error.cost,
            "submission_state": "submitted",
            "poll_started_ts": self.now,
        })
        self.assertEqual(result["cost"], 3)
        self.post.assert_called_once()
        submitted.assert_called_once()
        self.assertEqual(self.get.call_count, 2)
        self.assertEqual(
            [call.kwargs["headers"]["Authorization"] for call in self.get.call_args_list],
            ["Bearer key-one", "Bearer key-one"],
        )

    def test_invalid_status_json_is_retryable_without_resubmission(self):
        self.get.return_value = self.response(None)
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.run_video_generation(self.resume)
        self.assertTrue(raised.exception.retryable)
        self.assertFalse(raised.exception.submission_uncertain)
        self.assertEqual(raised.exception.cost, 1.0)
        self.post.assert_not_called()

    def test_submit_cost_survives_final_status_without_cost(self):
        self.post.return_value = self.response({"data": {"task_id": "upstream-1", "cost": "1.75"}})
        result = provider.run_video_generation(self.body)
        self.assertEqual(result["cost"], 1.75)

    def test_poll_cost_changes_are_persisted_and_zero_is_valid(self):
        costs = []
        self.get.side_effect = [
            self.response({"status": "running", "cost": 1.5}),
            self.response({"status": "running", "cost": 1.5}),
            self.response({"status": "running", "cost": None}),
            self.response({"status": "running", "cost": "0"}),
            self.response(self.success_payload),
        ]
        result = provider.run_video_generation({**self.resume, "cost_callback": costs.append})
        self.assertEqual(costs, [1.5, 0])
        self.assertEqual(result["cost"], 0)

    def test_terminal_failure_cost_is_persisted_and_carried_by_error(self):
        costs = []
        self.get.return_value = self.response({"status": "failed", "cost": "2.25", "error": "content rejected"})
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.run_video_generation({**self.resume, "cost_callback": costs.append})
        error = raised.exception
        self.assertEqual(costs, [2.25])
        self.assertEqual(error.cost, 2.25)
        self.assertEqual(error.upstream_task_id, "upstream-1")
        self.assertEqual(error.credential_id, self.credential_id)
        self.assertFalse(error.retryable)
        self.assertIn("content rejected", str(error))
        self.assertTrue(error.upstream_finished)

    def test_partial_cost_survives_polling_failure(self):
        self.get.side_effect = [
            self.response({"status": "running", "cost": 2.5}),
            self.response({"error": "unavailable"}, 503),
        ]
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.run_video_generation(self.resume)
        self.assertEqual(raised.exception.cost, 2.5)
        self.assertTrue(raised.exception.retryable)
        self.post.assert_not_called()

    def test_http_poll_error_retains_its_cost_including_zero(self):
        self.get.return_value = self.response({"error": "bad request", "data": {"cost": 0}}, 400)
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.run_video_generation(self.resume)
        self.assertEqual(raised.exception.cost, 0)

    def test_nonfinite_negative_boolean_or_invalid_cost_is_ignored(self):
        for value in (float("inf"), float("nan"), "Infinity", "NaN", -1, "-2.5", True, "invalid", ""):
            with self.subTest(value=value):
                self.get.return_value = self.response({**self.success_payload, "cost": value})
                self.assertEqual(provider.run_video_generation(self.resume)["cost"], 1.0)

    def test_cost_callback_failure_retains_latest_cost_and_task(self):
        for callback, retryable in (
            (mock.Mock(side_effect=RuntimeError("DB unavailable")), True),
            (mock.Mock(return_value=False), False),
        ):
            with self.subTest(retryable=retryable):
                self.get.return_value = self.response({"status": "running", "cost": 2})
                with self.assertRaises(provider.VideoGenerationProviderError) as raised:
                    provider.run_video_generation({**self.resume, "cost_callback": callback})
                self.assertEqual(raised.exception.cost, 2)
                self.assertEqual(raised.exception.upstream_task_id, "upstream-1")
                self.assertEqual(raised.exception.retryable, retryable)

    def test_statuses_are_matched_exactly_not_as_substrings(self):
        for state in ("incomplete", "not_completed", "unsuccessful", "error_handled", "failure_pending", "未完成", "无错误"):
            with self.subTest(state=state):
                self.get.return_value = self.response({"status": state})
                result = provider.get_video_generation_status("upstream-1", self.credential_id)
                self.assertEqual(result["status"], "unknown")
                self.assertFalse(result["finished"])
                self.assertFalse(result["failed"])

    def test_normalized_status_aliases(self):
        for state, normalized, finished, failed in (
            (" SUCCESS ", "success", True, False), ("COMPLETED", "success", True, False),
            ("成功", "success", True, False), ("FAILED", "failed", True, True),
            ("cancelled", "canceled", True, True), ("Canceled", "canceled", True, True),
            ("in-progress", "running", False, False), ("In Progress", "running", False, False),
            ("pending", "queued", False, False), ("finalizing", "finalizing", False, False),
        ):
            with self.subTest(state=state):
                self.get.return_value = self.response({"data": {"state": state}})
                result = provider.get_video_generation_status("upstream-1", self.credential_id)
                self.assertEqual((result["status"], result["finished"], result["failed"]), (normalized, finished, failed))

    def test_task_status_takes_precedence_over_envelope_success_and_progress(self):
        self.get.return_value = self.response({"status": "success", "data": {"status": "processing", "progress": "100%"}})
        result = provider.get_video_generation_status("upstream-1", self.credential_id)
        self.assertEqual(result["status"], "running")
        self.assertFalse(result["finished"])

    def test_terminal_failure_wins_over_success_group_and_url(self):
        self.get.return_value = self.response({**self.success_payload, "state": "failed", "status_group": "completed"})
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.run_video_generation(self.resume)
        self.assertFalse(raised.exception.retryable)

    def test_local_cancellation_does_not_abandon_known_upstream_task(self):
        progress = mock.Mock(return_value=False)
        self.get.side_effect = [self.response({"status": "running"}), self.response({**self.success_payload, "cost": 3})]
        result = provider.run_video_generation({
            **self.resume, "cancel_requested": True, "status": "canceled", "progress_callback": progress,
        })
        self.assertEqual(result["cost"], 3)
        self.assertEqual(self.get.call_count, 2)
        self.post.assert_not_called()
        progress.assert_called_once_with("polling")

    def test_upstream_cancellation_is_terminal_and_retains_cost(self):
        self.get.return_value = self.response({"status": "canceled", "cost": 1.5})
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.run_video_generation(self.resume)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.cost, 1.5)
        self.assertTrue(raised.exception.upstream_finished)
        self.get.assert_called_once()

    def test_expired_original_deadline_is_not_reset_on_resume(self):
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.run_video_generation({**self.resume, "poll_started_ts": self.now - 31})
        error = raised.exception
        self.assertFalse(error.retryable)
        self.assertFalse(error.upstream_finished)
        self.assertEqual(error.upstream_task_id, "upstream-1")
        self.assertEqual(error.credential_id, self.credential_id)
        self.assertEqual(error.cost, 1.0)
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_resume_only_uses_remaining_poll_budget(self):
        self.get.return_value = self.response({"status": "running", "cost": 4})
        costs = []
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.run_video_generation({**self.resume, "poll_started_ts": self.now - 29, "cost_callback": costs.append})
        self.get.assert_called_once()
        self.sleep.assert_called_once_with(1)
        self.assertEqual(costs, [4])
        self.assertEqual(raised.exception.cost, 4)
        self.assertFalse(raised.exception.retryable)
        self.assertFalse(raised.exception.upstream_finished)
        self.post.assert_not_called()

    def test_invalid_poll_started_timestamp_fails_before_network(self):
        for value in (0, -1, True, "invalid", float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(provider.VideoGenerationProviderError):
                provider.run_video_generation({**self.resume, "poll_started_ts": value})
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_missing_result_url_has_bounded_grace_period(self):
        self.settings["poll_timeout_secs"] = 180
        self.get.return_value = self.response({"status": "completed", "cost": 2})
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.run_video_generation(self.resume)
        self.assertIn("without video_url", str(raised.exception))
        self.assertEqual(raised.exception.cost, 2)
        self.assertEqual(raised.exception.upstream_task_id, "upstream-1")
        self.assertLessEqual(self.get.call_count, 31)
        self.post.assert_not_called()

    def test_keys_echoed_by_upstream_are_redacted_in_results_and_errors(self):
        self.post.return_value = self.response({"task_id": "upstream-1", "echo": {"authorization": "Bearer key-one"}})
        self.get.return_value = self.response({**self.success_payload, "echo": ["key-two", "key-one"]})
        result = provider.run_video_generation(self.body)
        self.assertNotIn("key-one", repr(result))
        self.assertNotIn("key-two", repr(result))
        self.get.return_value = self.response({"error": "invalid key-two or key-one"}, 400)
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.run_video_generation(self.resume)
        self.assertNotIn("key-one", str(raised.exception))
        self.assertNotIn("key-two", str(raised.exception))

    def test_checkpoint_runs_before_requests_and_after_sleep(self):
        events = []
        responses = iter([self.response({"status": "running"}), self.response(self.success_payload)])
        self.post.side_effect = lambda *args, **kwargs: events.append("post") or self.response({"task_id": "upstream-1"})
        self.get.side_effect = lambda *args, **kwargs: events.append("get") or next(responses)
        self.sleep.side_effect = lambda seconds: events.append("sleep") or self.advance_time(seconds)
        provider.run_video_generation({
            **self.body,
            "checkpoint_callback": lambda: events.append("checkpoint"),
            "submission_started_callback": lambda: events.append("started"),
            "submission_callback": lambda *args, **kwargs: events.append("persisted"),
        })
        self.assertEqual(events, [
            "checkpoint", "started", "post", "persisted", "checkpoint", "get",
            "sleep", "checkpoint", "checkpoint", "get",
        ])

    def test_checkpoint_lease_loss_before_post_propagates_unchanged(self):
        class LeaseLost(RuntimeError):
            pass

        lost = LeaseLost("worker lease lost")
        started = mock.Mock()
        with self.assertRaises(LeaseLost) as raised:
            provider.run_video_generation({
                **self.body, "checkpoint_callback": mock.Mock(side_effect=lost),
                "submission_started_callback": started,
            })
        self.assertIs(raised.exception, lost)
        started.assert_not_called()
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_checkpoint_lease_loss_before_get_propagates_unchanged(self):
        class LeaseLost(RuntimeError):
            pass

        lost = LeaseLost("worker lease lost")
        with self.assertRaises(LeaseLost) as raised:
            provider.run_video_generation({**self.resume, "checkpoint_callback": mock.Mock(side_effect=lost)})
        self.assertIs(raised.exception, lost)
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_checkpoint_lease_loss_after_sleep_prevents_next_get(self):
        class LeaseLost(RuntimeError):
            pass

        lost = LeaseLost("worker lease lost")
        checkpoint = mock.Mock(side_effect=[None, lost])
        self.get.return_value = self.response({"status": "running"})
        with self.assertRaises(LeaseLost) as raised:
            provider.run_video_generation({**self.resume, "checkpoint_callback": checkpoint})
        self.assertIs(raised.exception, lost)
        self.assertEqual(checkpoint.call_count, 2)
        self.sleep.assert_called_once()
        self.get.assert_called_once()
        self.post.assert_not_called()

    def test_poll_network_failure_does_not_claim_upstream_finished(self):
        self.get.side_effect = provider.requests.exceptions.Timeout("read timeout")
        with self.assertRaises(provider.VideoGenerationProviderError) as raised:
            provider.run_video_generation(self.resume)
        self.assertFalse(raised.exception.upstream_finished)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.upstream_task_id, "upstream-1")


if __name__ == "__main__":
    unittest.main()
