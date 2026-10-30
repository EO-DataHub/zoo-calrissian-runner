import os
import unittest
from unittest import mock

import yaml

import zoo_calrissian_runner
from zoo_calrissian_runner import ZooCalrissianRunner, get_failure_reason

INCIDENT_LOG = """\
2026-10-22 09:14:02,101 - calrissian.main - INFO - Checking for illegal steps
Traceback (most recent call last):
  File "/usr/local/bin/calrissian", line 8, in <module>
    sys.exit(main())
  File "/usr/local/lib/python3.10/site-packages/calrissian/main.py", line 123, in check_for_illegal_steps
    for step_id in list(item_steps.keys()):
AttributeError: 'list' object has no attribute 'keys'
"""

ILLEGAL_STEP_LOG = """\
Traceback (most recent call last):
  File "/usr/local/lib/python3.10/site-packages/calrissian/main.py", line 127, in check_for_illegal_steps
    raise WorkflowException(f"Illegal step included in workflow: {step_id}")
cwltool.errors.WorkflowException: Illegal step included in workflow: node_stage_out_x
"""

NESTED_STEP_LOG = """\
INFO [workflow main] start
INFO [step node_x] completed permanentFail
WARNING [workflow wf] completed permanentFail
INFO [step on_stage] completed permanentFail
ERROR Workflow error, try again with --debug for more information
"""


class TestGetFailureReason(unittest.TestCase):
    def test_incident_traceback(self):
        self.assertEqual(
            get_failure_reason(INCIDENT_LOG),
            "AttributeError: 'list' object has no attribute 'keys'",
        )

    def test_illegal_step(self):
        self.assertEqual(
            get_failure_reason(ILLEGAL_STEP_LOG),
            "cwltool.errors.WorkflowException: "
            "Illegal step included in workflow: node_stage_out_x",
        )

    def test_innermost_failed_step(self):
        self.assertEqual(
            get_failure_reason(NESTED_STEP_LOG), "[step node_x] completed permanentFail"
        )

    def test_ansi_error_line(self):
        log = "\x1b[1;30mINFO\x1b[0m start\n\x1b[31mERROR\x1b[0m something broke\n"
        self.assertEqual(get_failure_reason(log), "ERROR something broke")

    def test_tool_traceback_is_ignored(self):
        log = (
            "[pod] Traceback (most recent call last):\n"
            "[pod] ValueError: bad input\n"
            "ERROR job failed\n"
        )
        self.assertEqual(get_failure_reason(log), "ERROR job failed")

    def test_empty(self):
        self.assertIsNone(get_failure_reason(None))
        self.assertIsNone(get_failure_reason(""))
        self.assertIsNone(get_failure_reason("INFO all good\n"))

    def test_truncated(self):
        reason = get_failure_reason("ERROR " + "x" * 1000)
        self.assertEqual(len(reason), 500)
        self.assertTrue(reason.endswith("..."))


@mock.patch.dict(
    os.environ,
    {
        "DEFAULT_VOLUME_SIZE": "10000",
        "DEFAULT_MAX_CORES": "2",
        "DEFAULT_MAX_RAM": "4096",
        "KEEP_SESSION": "false",
    },
)
class TestExecuteFailure(unittest.TestCase):
    def setUp(self):
        with open(os.path.join("tests", "app-s2-composites.0.1.0.cwl")) as stream:
            self.cwl = yaml.safe_load(stream)
        self.conf = {"lenv": {"Identifier": "dnbr", "usid": "abc"}, "main": {"tmpPath": "/tmp"}}
        self.inputs = {
            "calling_workspace": {"value": "ws-a"},
            "executing_workspace": {"value": "ws-a"},
        }
        self.handler = mock.Mock()
        self.handler.get_additional_parameters.return_value = {}
        self.calls = []

        patches = {
            name: mock.patch.object(zoo_calrissian_runner, name)
            for name in ["CalrissianContext", "CalrissianJob", "CalrissianExecution"]
        }
        self.mocks = {name: patch.start() for name, patch in patches.items()}
        for patch in patches.values():
            self.addCleanup(patch.stop)

        self.session = self.mocks["CalrissianContext"].return_value
        self.session.dispose_job_resources.side_effect = lambda: self.calls.append(
            "dispose"
        )
        self.execution = self.mocks["CalrissianExecution"].return_value
        self.execution.get_log.side_effect = self.get_log
        self.log = NESTED_STEP_LOG

    def get_log(self):
        self.calls.append("get_log")
        return self.log

    def run_execute(self, succeeded):
        self.execution.is_succeeded.return_value = succeeded
        runner = ZooCalrissianRunner(
            cwl=self.cwl,
            conf=self.conf,
            inputs=self.inputs,
            outputs={"stac": {}},
            execution_handler=self.handler,
        )
        runner.wrap = mock.Mock(return_value={})
        runner.assert_parameters = mock.Mock(return_value=True)
        return runner, runner.execute()

    def test_failed_run(self):
        runner, result = self.run_execute(succeeded=False)
        self.assertEqual(result, zoo_calrissian_runner.zoo.SERVICE_FAILED)
        self.assertEqual(self.calls, ["get_log", "dispose"])
        self.assertEqual(runner.failure_reason, "[step node_x] completed permanentFail")
        self.assertEqual(
            self.conf["lenv"]["message"],
            "execution failed: [step node_x] completed permanentFail",
        )
        self.handler.post_execution_hook.assert_not_called()
        self.execution.get_output.assert_not_called()
        self.assertIsNone(self.handler.handle_outputs.call_args.kwargs["output"])

    def test_failed_run_usage_report_error(self):
        self.execution.get_usage_report.side_effect = RuntimeError("helper pod")
        _, result = self.run_execute(succeeded=False)
        self.assertEqual(result, zoo_calrissian_runner.zoo.SERVICE_FAILED)
        self.session.dispose_job_resources.assert_called_once()

    def test_failed_run_log_error(self):
        self.execution.get_log.side_effect = RuntimeError("pod gone")
        runner, result = self.run_execute(succeeded=False)
        self.assertEqual(result, zoo_calrissian_runner.zoo.SERVICE_FAILED)
        self.assertIsNone(runner.failure_reason)
        self.assertEqual(self.conf["lenv"]["message"], "execution failed")

    def test_successful_run(self):
        output = {"StacCatalogUri": "s3://bucket/catalog.json"}
        self.execution.get_output.return_value = output
        runner, result = self.run_execute(succeeded=True)
        self.assertEqual(result, zoo_calrissian_runner.zoo.SERVICE_SUCCEEDED)
        self.assertIsNone(runner.failure_reason)
        self.assertEqual(self.conf["lenv"]["message"], "execution successful")
        self.assertEqual(self.execution.get_log.call_count, 1)
        self.assertEqual(self.handler.handle_outputs.call_args.kwargs["output"], output)
        self.assertEqual(
            self.handler.post_execution_hook.call_args.kwargs["log"], NESTED_STEP_LOG
        )

    def test_job_has_no_retries(self):
        self.run_execute(succeeded=True)
        kwargs = self.mocks["CalrissianJob"].call_args.kwargs
        self.assertEqual(kwargs["backoff_limit"], 0)
        self.assertTrue(kwargs["retry_on_disruption"])


if __name__ == "__main__":
    unittest.main()
