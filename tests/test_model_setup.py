import hashlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "install_embedding_model",
    Path(__file__).parents[1] / "tools" / "install_embedding_model.py",
)
model_setup = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(model_setup)


class ClosingBytesIO(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class ModelSetupTests(unittest.TestCase):
    def test_installer_uses_pinned_urls_and_verifies_every_file(self):
        payloads = {"model.onnx": b"model", "tokenizer/tokenizer.json": b"tokenizer"}
        files = {
            destination: (destination, hashlib.sha256(value).hexdigest())
            for destination, value in payloads.items()
        }
        requested = []

        def opener(url):
            requested.append(url)
            name = url.rsplit("/", 1)[-1]
            source = "model.onnx" if name == "model.onnx" else "tokenizer/tokenizer.json"
            return ClosingBytesIO(payloads[source])

        with tempfile.TemporaryDirectory() as temporary, patch.object(model_setup, "FILES", files):
            destination = Path(temporary) / "model"
            result = model_setup.install(destination, opener=opener)
            self.assertEqual("installed", result["status"])
            self.assertEqual([], model_setup.problems(destination))
            self.assertTrue(all(model_setup.MODEL_REVISION in url for url in requested))
            self.assertEqual("already-installed", model_setup.install(destination, opener=opener)["status"])
            self.assertEqual(2, len(requested))

    def test_corruption_is_reported(self):
        value = b"expected"
        files = {"model.onnx": ("model.onnx", hashlib.sha256(value).hexdigest())}
        with tempfile.TemporaryDirectory() as temporary, patch.object(model_setup, "FILES", files):
            destination = Path(temporary)
            (destination / "model.onnx").write_bytes(b"wrong")
            self.assertEqual(["checksum mismatch for model.onnx"], model_setup.problems(destination))


if __name__ == "__main__":
    unittest.main()
