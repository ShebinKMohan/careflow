"""Temperature scaling: one scalar T that minimises the validation NLL of softmax(logits / T)."""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import log_softmax, softmax


def probabilities(logits: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    return softmax(logits / temperature, axis=1)


def nll(logits: np.ndarray, targets: np.ndarray, temperature: float) -> float:
    """Mean negative log-likelihood; `targets` are class indices (0-based)."""
    log_p = log_softmax(logits / temperature, axis=1)
    return float(-log_p[np.arange(len(targets)), targets].mean())


def fit_temperature(logits: np.ndarray, targets: np.ndarray) -> float:
    result = minimize_scalar(
        lambda t: nll(logits, targets, t), bounds=(0.05, 10.0), method="bounded"
    )
    return float(result.x)
