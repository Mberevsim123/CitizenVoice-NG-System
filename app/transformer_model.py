"""
Multi-task transformer classifier used by CitizenVoice.

One pre-trained sentence encoder (MiniLM-L6) is shared by three small classification
heads: sentiment, urgency and category. Sharing the encoder means one forward pass
answers all three questions, which keeps inference fast on a CPU-only server.

Saved layout (models/transformer/):
    encoder/      Hugging Face encoder weights (safetensors) and tokenizer
    heads.pt      state dict of the three linear heads
    labels.json   label order per task, max sequence length, base model name
"""
import json
from pathlib import Path

import torch
from torch import nn
from transformers import AutoModel, AutoTokenizer  # noqa: F401

BASE_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Eager attention is used on purpose: with torch 2.2 on older x86 CPUs the fused SDPA kernel
# can stall for many minutes on its first padded batch in inference mode.
ATTN_IMPL = "eager"


def load_encoder(name_or_path):
    return AutoModel.from_pretrained(name_or_path, attn_implementation=ATTN_IMPL)


class MultiTaskClassifier(nn.Module):
    def __init__(self, encoder, n_labels: dict, dropout: float = 0.1):
        super().__init__()
        self.encoder = encoder
        hidden = encoder.config.hidden_size
        self.drop = nn.Dropout(dropout)
        self.heads = nn.ModuleDict({task: nn.Linear(hidden, n) for task, n in n_labels.items()})

    def forward(self, input_ids, attention_mask):
        out = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
        mask = attention_mask.unsqueeze(-1).float()
        pooled = (out * mask).sum(1) / mask.sum(1).clamp(min=1e-6)   # mean pooling
        pooled = self.drop(pooled)
        return {task: head(pooled) for task, head in self.heads.items()}


def save(model: MultiTaskClassifier, tokenizer, labels: dict, max_len: int, out_dir: Path, base=BASE_MODEL):
    out_dir = Path(out_dir)
    (out_dir / "encoder").mkdir(parents=True, exist_ok=True)
    model.encoder.save_pretrained(out_dir / "encoder", safe_serialization=True)
    tokenizer.save_pretrained(out_dir / "encoder")
    torch.save(model.heads.state_dict(), out_dir / "heads.pt")
    (out_dir / "labels.json").write_text(json.dumps({"labels": labels, "max_len": max_len, "base_model": base},
                                                    indent=1))


class TransformerPredictor:
    """Loads a saved multi-task model for inference."""

    def __init__(self, model_dir: Path):
        model_dir = Path(model_dir)
        meta = json.loads((model_dir / "labels.json").read_text())
        self.labels = meta["labels"]
        self.max_len = meta["max_len"]
        self.base_model = meta.get("base_model", BASE_MODEL)
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir / "encoder")
        encoder = load_encoder(model_dir / "encoder")
        self.model = MultiTaskClassifier(encoder, {t: len(v) for t, v in self.labels.items()})
        self.model.heads.load_state_dict(torch.load(model_dir / "heads.pt", map_location="cpu"))
        self.model.eval()

    @torch.no_grad()
    def predict_proba(self, texts: list, batch_size: int = 32) -> dict:
        """Returns {task: [ {label: probability}, ... ]} for each text."""
        out = {t: [] for t in self.labels}
        for i in range(0, len(texts), batch_size):
            enc = self.tokenizer(texts[i:i + batch_size], truncation=True, max_length=self.max_len,
                                 padding=True, return_tensors="pt")
            logits = self.model(enc["input_ids"], enc["attention_mask"])
            for task, lab in self.labels.items():
                probs = torch.softmax(logits[task], dim=-1).tolist()
                out[task].extend(dict(zip(lab, row)) for row in probs)
        return out
