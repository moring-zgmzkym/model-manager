from modeldock.comfyui import build_graph


def test_build_graph_substitutes_typed_values():
    template = {
        "1": {"class_type": "X", "inputs": {"width": "{{width}}", "text": "prefix {{prompt}}"}},
    }
    graph = build_graph(template, {"width": 864, "prompt": "a cat"})
    assert graph["1"]["inputs"]["width"] == 864
    assert graph["1"]["inputs"]["text"] == "prefix a cat"
    # template must not be mutated
    assert template["1"]["inputs"]["width"] == "{{width}}"


def test_build_graph_drops_empty_optional_nodes():
    template = {
        "1": {"class_type": "Loader", "inputs": {"name": "m.gguf"}},
        "6": {"class_type": "Video", "inputs": {"first_frame": ["20", 0], "last_frame": ["21", 0]}},
        "20": {"class_type": "LoadImage", "_if": "first_frame", "inputs": {"image": "{{first_frame}}"}},
        "21": {"class_type": "LoadImage", "_if": "last_frame", "inputs": {"image": "{{last_frame}}"}},
    }
    graph = build_graph(template, {"first_frame": "a.png", "last_frame": ""})
    assert set(graph) == {"1", "6", "20"}
    assert graph["6"]["inputs"]["first_frame"] == ["20", 0]
    assert "last_frame" not in graph["6"]["inputs"]
    assert "_if" not in graph["20"]


def test_build_graph_removes_nested_references():
    template = {
        "5": {"class_type": "Encode", "inputs": {"images": [["9", 0], ["8", 0]]}},
        "8": {"class_type": "LoadImage", "inputs": {"image": "keep.png"}},
        "9": {"class_type": "LoadImage", "_if": "ref", "inputs": {"image": "{{ref}}"}},
    }
    graph = build_graph(template, {"ref": ""})
    assert set(graph) == {"5", "8"}
    assert graph["5"]["inputs"]["images"] == [["8", 0]]
