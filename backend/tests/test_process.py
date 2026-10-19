import asyncio

from modeldock.process import ProcessManager, ServiceSpec


def test_stop_all_skips_keep_running_models():
    pm = ProcessManager(lambda: "python")
    kept = pm.register(ServiceSpec(key="a", model_id="a", name="A", command="x"))
    stopped = pm.register(ServiceSpec(key="b", model_id="b", name="B", command="x"))
    kept.status = "running"
    stopped.status = "running"

    asyncio.run(pm.stop_all(managed_only=False, skip_model_ids={"a"}))

    assert kept.status == "running"
    assert stopped.status == "stopped"
