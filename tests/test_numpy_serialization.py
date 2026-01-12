import json

import numpy as np
import pytest

from beavr_network.network.service_server import NumpyJSONEncoder


def test_numpy_json_encoder():
    """Test that NumpyJSONEncoder correctly serializes NumPy types."""
    data = {
        "int": np.int64(10),
        "float": np.float64(3.14),
        "array": np.array([1, 2, 3]),
        "nested": {"val": np.int32(5)},
    }

    # Standard json.dumps should fail (verify this first)
    with pytest.raises(TypeError, match="is not JSON serializable"):
        json.dumps(data)

    # Our encoder should succeed
    serialized = json.dumps(data, cls=NumpyJSONEncoder)
    loaded = json.loads(serialized)

    assert loaded["int"] == 10
    assert isinstance(loaded["int"], int)

    assert loaded["float"] == pytest.approx(3.14)
    assert isinstance(loaded["float"], float)

    assert loaded["array"] == [1, 2, 3]
    assert isinstance(loaded["array"], list)

    assert loaded["nested"]["val"] == 5
    assert isinstance(loaded["nested"]["val"], int)


def test_numpy_json_encoder_passthrough():
    """Test that NumpyJSONEncoder still handles standard types."""
    data = {"str": "hello", "int": 42, "list": [1, 2, 3], "dict": {"a": 1}}

    serialized = json.dumps(data, cls=NumpyJSONEncoder)
    loaded = json.loads(serialized)
    assert loaded == data
