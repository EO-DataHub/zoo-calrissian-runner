import os
import unittest
from unittest import mock

import zoo_calrissian_runner
from zoo_calrissian_runner import ZooCalrissianRunner


class _StopAfterJobCreated(Exception):
    pass


class TestCalrissianJobBackoff(unittest.TestCase):
    """The runner must create the Calrissian Job with no Kubernetes retries.

    A retried pod reuses the first run's working directory and fails, so a
    lost pod has to fail the job instead of starting a second run.
    """

    def _runner(self):
        runner = ZooCalrissianRunner.__new__(ZooCalrissianRunner)
        runner.handler = mock.MagicMock()
        runner.zoo_conf = mock.MagicMock(conf={"main": {"tmpPath": "/tmp"}})
        runner.storage_class = "test"
        runner.calling_workspace = "ws-calling"
        runner.executing_workspace = "ws-executing"
        runner.job_id = "job-123"
        runner.monitor_interval = 1
        runner.update_status = mock.MagicMock()
        runner.assert_parameters = mock.MagicMock(return_value=True)
        runner.wrap = mock.MagicMock(return_value={"cwlVersion": "v1.0"})
        runner.get_namespace_name = mock.MagicMock(return_value="ns")
        runner.get_volume_size = mock.MagicMock(return_value="1Gi")
        runner.get_processing_parameters = mock.MagicMock(return_value={})
        runner.get_max_cores = mock.MagicMock(return_value=1)
        runner.get_max_ram = mock.MagicMock(return_value="1G")
        return runner

    def test_job_is_created_with_backoff_limit_zero(self):
        runner = self._runner()

        with mock.patch.dict(os.environ, {"KEEP_SESSION": "false"}), \
                mock.patch.object(zoo_calrissian_runner, "CalrissianContext") as context_cls, \
                mock.patch.object(
                    zoo_calrissian_runner,
                    "CalrissianJob",
                    side_effect=_StopAfterJobCreated,
                ) as job_cls:
            with self.assertRaises(_StopAfterJobCreated):
                runner.execute()

        job_cls.assert_called_once()
        self.assertEqual(job_cls.call_args.kwargs["backoff_limit"], 0)
        # Resources are still cleaned up when job creation fails.
        context_cls.return_value.dispose_job_resources.assert_called_once()


if __name__ == "__main__":
    unittest.main()
