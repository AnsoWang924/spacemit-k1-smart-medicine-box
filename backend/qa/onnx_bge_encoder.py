"""无transformers依赖的BGE ONNX编码器，适用于K1的SpaceMIT ONNX Runtime。"""
import os
import unicodedata
from pathlib import Path

import numpy as np


def _is_whitespace(char):
    return char in " \t\n\r" or unicodedata.category(char) == "Zs"


def _is_control(char):
    return char not in "\t\n\r" and unicodedata.category(char) in ("Cc", "Cf")


def _is_punctuation(char):
    code = ord(char)
    if 33 <= code <= 47 or 58 <= code <= 64 or 91 <= code <= 96 or 123 <= code <= 126:
        return True
    return unicodedata.category(char).startswith("P")


def _is_chinese_char(code):
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
        or 0xF900 <= code <= 0xFAFF
        or 0x2F800 <= code <= 0x2FA1F
    )


def _whitespace_tokenize(text):
    text = text.strip()
    return text.split() if text else []


class WordPieceTokenizer:
    def __init__(self, vocab_path, do_lower_case=True):
        self.vocab = {}
        with Path(vocab_path).open("r", encoding="utf-8") as handle:
            for index, token in enumerate(handle):
                self.vocab[token.rstrip("\n\r")] = index
        self.do_lower_case = do_lower_case
        self.unk_token = "[UNK]"
        self.cls_id = self.vocab["[CLS]"]
        self.sep_id = self.vocab["[SEP]"]
        self.pad_id = self.vocab["[PAD]"]
        self.unk_id = self.vocab[self.unk_token]

    def _clean_text(self, text):
        output = []
        for char in text:
            code = ord(char)
            if code == 0 or code == 0xFFFD or _is_control(char):
                continue
            output.append(" " if _is_whitespace(char) else char)
        return "".join(output)

    def _tokenize_chinese(self, text):
        output = []
        for char in text:
            if _is_chinese_char(ord(char)):
                output.extend((" ", char, " "))
            else:
                output.append(char)
        return "".join(output)

    @staticmethod
    def _strip_accents(text):
        return "".join(
            char for char in unicodedata.normalize("NFD", text)
            if unicodedata.category(char) != "Mn"
        )

    @staticmethod
    def _split_punctuation(text):
        output = []
        current = []
        for char in text:
            if _is_punctuation(char):
                if current:
                    output.append("".join(current))
                    current = []
                output.append(char)
            else:
                current.append(char)
        if current:
            output.append("".join(current))
        return output

    def basic_tokenize(self, text):
        text = self._tokenize_chinese(self._clean_text(str(text)))
        output = []
        for token in _whitespace_tokenize(text):
            if self.do_lower_case:
                token = self._strip_accents(token.lower())
            output.extend(self._split_punctuation(token))
        return _whitespace_tokenize(" ".join(output))

    def wordpiece_tokenize(self, token):
        chars = list(token)
        if len(chars) > 100:
            return [self.unk_token]
        pieces = []
        start = 0
        while start < len(chars):
            end = len(chars)
            current = None
            while start < end:
                piece = "".join(chars[start:end])
                if start > 0:
                    piece = "##" + piece
                if piece in self.vocab:
                    current = piece
                    break
                end -= 1
            if current is None:
                return [self.unk_token]
            pieces.append(current)
            start = end
        return pieces

    def encode(self, text, max_length=64):
        tokens = []
        for token in self.basic_tokenize(text):
            tokens.extend(self.wordpiece_tokenize(token))
        tokens = tokens[: max_length - 2]
        ids = [self.cls_id] + [self.vocab.get(token, self.unk_id) for token in tokens] + [self.sep_id]
        attention = [1] * len(ids)
        return ids, attention


class OnnxBGEEncoder:
    def __init__(self, model_path, vocab_path=None, max_length=64, threads=None):
        import onnxruntime as ort

        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise FileNotFoundError(self.model_path)
        vocab_path = Path(vocab_path) if vocab_path else self.model_path.parent / "vocab.txt"
        self.tokenizer = WordPieceTokenizer(vocab_path)
        self.max_length = max_length
        options = ort.SessionOptions()
        # K1实测INT8单线程均值/P95都优于2和4线程，同时给LLM留出更多调度余量。
        options.intra_op_num_threads = int(threads or os.getenv("MEDICINE_QA_EMBED_THREADS", "1"))
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(self.model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

    def encode(self, sentences, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False):
        del show_progress_bar
        if isinstance(sentences, str):
            sentences = [sentences]
        encoded = [self.tokenizer.encode(text, self.max_length) for text in sentences]
        batch_length = max(len(item[0]) for item in encoded)
        input_ids = np.asarray(
            [item[0] + [self.tokenizer.pad_id] * (batch_length - len(item[0])) for item in encoded],
            dtype=np.int64,
        )
        attention_mask = np.asarray(
            [item[1] + [0] * (batch_length - len(item[1])) for item in encoded],
            dtype=np.int64,
        )
        token_type_ids = np.zeros_like(input_ids)
        embeddings = self.session.run(
            ["sentence_embedding"],
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )[0].astype(np.float32, copy=False)
        if normalize_embeddings:
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            embeddings = embeddings / np.maximum(norms, 1e-12)
        return embeddings if convert_to_numpy else embeddings.tolist()
